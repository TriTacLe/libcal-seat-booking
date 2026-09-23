"""Holding a UCT session.

Availability is public; booking is not. Playwright exists here only to get
through UCT SSO once and keep the cookie jar afterwards. Everything after login
runs as fetch() inside the authenticated page, so cookies and CSRF stay the
browser's problem.

The UCT chain has not been walked yet, so `login` records what it sees (hosts,
cookie names, never values) into data/debug/. That recording is what the
automatic re-auth gets built from, rather than a guess.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from playwright.sync_api import Browser, BrowserContext, Page, Playwright

from uctbook.clock import stamp, utc_now

logger = logging.getLogger(__name__)

STATE_FILE = Path("data/storage_state.json")
DEBUG_DIR = Path("data/debug")
ENTRY_URL = "https://uct.ac.za.libcal.com/r/new"
LOGIN_TIMEOUT_MS = 600_000  # MFA can take a while

NotifyFn = Callable[[str, bytes], None]

LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
    "--js-flags=--max-old-space-size=512",
]


class LoginTimeoutError(RuntimeError):
    pass


class SessionExpiredError(RuntimeError):
    pass


def host_of(url: str) -> str:
    return urlparse(url).hostname or ""


def password_for(user: str) -> str:
    """File first, then the system keyring, then the environment. The server
    gets a mounted file; the laptop gets secret-tool."""
    path = os.environ.get("UCT_PASSWORD_FILE", "")
    if path:
        try:
            return Path(path).read_text().strip()
        except OSError:
            pass
    if user:
        try:
            found = subprocess.run(
                ["secret-tool", "lookup", "service", "uctbook", "username", user],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if found.returncode == 0 and found.stdout.strip():
                return found.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return os.environ.get("UCT_PASSWORD", "")


def block_passkeys(ctx: BrowserContext) -> None:
    """A passkey prompt deadlocks a headless run, so reject the API outright."""
    ctx.add_init_script(
        """
        Object.defineProperty(navigator, 'credentials', {
            get: () => ({
                get: () => Promise.reject(new DOMException('NotAllowedError', 'blocked')),
                create: () => Promise.reject(new DOMException('NotAllowedError', 'blocked')),
            }),
            configurable: true,
        });
        """
    )


def goto_with_retry(page: Page, url: str, attempts: int = 3) -> None:
    """The first run after the server wakes from suspend hits a dead resolver."""
    for i in range(attempts):
        try:
            page.goto(url, wait_until="domcontentloaded")
            return
        except Exception:
            if i == attempts - 1:
                raise
            wait = 10 * (2**i)
            logger.info("goto %s failed, retrying in %ds", url, wait)
            page.wait_for_timeout(wait * 1000)


# Analytics sets plain cookies on the LibCal domain for anonymous visitors, so
# presence alone proves nothing. A patron session is httpOnly; analytics is not.
ANALYTICS_PREFIXES = ("_ga", "_gid", "_gat", "_fbp", "_hj")


def session_cookies(ctx: BrowserContext) -> list[str]:
    """Cookies that indicate a signed-in patron rather than a page view."""
    found = []
    for cookie in ctx.cookies():
        name = str(cookie.get("name", ""))
        domain = str(cookie.get("domain", ""))
        if "libcal.com" not in domain:
            continue
        if name.startswith(ANALYTICS_PREFIXES):
            continue
        if not cookie.get("httpOnly"):
            continue
        found.append(name)
    return found


def is_authenticated(ctx: BrowserContext) -> bool:
    return bool(session_cookies(ctx))


def wait_for_login(
    ctx: BrowserContext,
    page: Page,
    timeout_ms: int = LOGIN_TIMEOUT_MS,
    step_ms: int = 2_000,
) -> bool:
    """Poll until a LibCal session cookie appears.

    Polling rather than waiting on a keypress, so the login works the same when
    something other than a person is driving the terminal. The session is saved
    on every poll, so a browser closed early still leaves a usable jar.
    """
    waited = 0
    while waited < timeout_ms:
        try:
            page.wait_for_timeout(step_ms)
        except Exception:
            return is_authenticated(ctx)
        waited += step_ms
        try:
            cookies = session_cookies(ctx)
        except Exception:
            return False
        if cookies:
            save_session(ctx)
            logger.info("LibCal session cookie(s): %s", ", ".join(sorted(set(cookies))))
            return True
        if waited % 30_000 == 0:
            logger.info("still waiting for sign-in (%ds)", waited // 1000)
    return False


def launch(p: Playwright, headless: bool = True) -> Browser:
    return p.chromium.launch(headless=headless, args=LAUNCH_ARGS)


def new_context(browser: Browser, reuse_state: bool = True) -> BrowserContext:
    if reuse_state and STATE_FILE.exists():
        ctx = browser.new_context(storage_state=str(STATE_FILE))
    else:
        ctx = browser.new_context()
    block_passkeys(ctx)
    return ctx


def save_session(ctx: BrowserContext) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    ctx.storage_state(path=str(STATE_FILE))


def open_session(p: Playwright, headless: bool = True) -> tuple[Browser, BrowserContext, Page]:
    """Restore the stored session. Raises if it is gone, because re-authenticating
    needs a human until the UCT chain has been recorded."""
    browser = launch(p, headless=headless)
    ctx = new_context(browser)
    page = ctx.new_page()
    goto_with_retry(page, ENTRY_URL)
    page.wait_for_timeout(2000)
    if not STATE_FILE.exists():
        raise SessionExpiredError("No stored session. Run 'uct login' first.")
    return browser, ctx, page


def close_session(browser: Browser, ctx: BrowserContext | None, page: Page | None) -> None:
    for closer in (page, ctx, browser):
        try:
            if closer is not None:
                closer.close()
        except Exception:
            pass


class ChainRecorder:
    """Every host the login passes through, and which cookies each step set.

    Names and flags only, never values: this file is meant to be readable and
    pasteable, and it describes the shape of the chain, not the secrets in it.
    """

    def __init__(self, page: Page, ctx: BrowserContext) -> None:
        self.page = page
        self.ctx = ctx
        self.steps: list[dict[str, object]] = []
        self._last_host = ""
        page.on("framenavigated", self._on_navigate)

    def _on_navigate(self, frame: object) -> None:
        try:
            url = frame.url  # type: ignore[attr-defined]
        except Exception:
            return
        host = host_of(url)
        if not host or host == self._last_host:
            return
        self._last_host = host
        self.steps.append(
            {
                "at": utc_now().isoformat(timespec="seconds"),
                "host": host,
                "path": urlparse(url).path,
            }
        )
        logger.info("login step: %s", host)

    def cookie_names(self) -> list[dict[str, object]]:
        out: list[dict[str, object]] = []
        for cookie in self.ctx.cookies():
            out.append(
                {
                    "name": cookie.get("name"),
                    "domain": cookie.get("domain"),
                    "httpOnly": cookie.get("httpOnly"),
                    "secure": cookie.get("secure"),
                    "value_length": len(str(cookie.get("value", ""))),
                }
            )
        return out

    def write(self, label: str = "login") -> Path:
        stamped = stamp()
        out = DEBUG_DIR / f"{stamped}-{label}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "chain.json").write_text(
            json.dumps({"steps": self.steps, "cookies": self.cookie_names()}, indent=2)
        )
        try:
            (out / "page.html").write_text(self.page.content())
            (out / "page.png").write_bytes(self.page.screenshot(full_page=True))
        except Exception as e:
            logger.info("could not dump the final page: %s", e)
        return out

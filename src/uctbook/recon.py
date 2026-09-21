"""Capturing the booking calls LibCal makes.

The submit endpoint is the one part of the flow that cannot be read off the
public site, so it gets recorded once from a real booking made by hand in a
visible browser. Requests and responses are written in full, minus anything
that identifies the account, so the capture can be committed as a fixture and
replayed offline.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, Request, Response

from uctbook.clock import stamp, utc_now

logger = logging.getLogger(__name__)

RECON_DIR = Path("data/recon")
INTERESTING = re.compile(r"/(ajax/space|spaces/availability|equipment|r/accessible)")
DROP_HEADERS = {"cookie", "set-cookie", "authorization", "proxy-authorization"}
MAX_BODY = 256_000


def _redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "REDACTED")
    return text


def _headers(raw: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in raw.items() if k.lower() not in DROP_HEADERS}


class Capture:
    """Records every LibCal call the page makes, in order."""

    def __init__(self, page: Page, secrets: list[str] | None = None) -> None:
        self.page = page
        self.secrets = [s for s in (secrets or []) if s]
        self.calls: list[dict[str, Any]] = []
        page.on("request", self._on_request)
        page.on("response", self._on_response)

    def _on_request(self, request: Request) -> None:
        if not INTERESTING.search(request.url):
            return
        self.calls.append(
            {
                "at": utc_now().isoformat(timespec="milliseconds"),
                "method": request.method,
                "url": request.url,
                "headers": _headers(request.headers),
                "body": _redact(request.post_data or "", self.secrets),
                "status": None,
                "response_headers": {},
                "response": "",
            }
        )
        logger.info("%s %s", request.method, request.url.split("?")[0])

    def _on_response(self, response: Response) -> None:
        if not INTERESTING.search(response.url):
            return
        for call in reversed(self.calls):
            if call["url"] == response.url and call["status"] is None:
                call["status"] = response.status
                call["response_headers"] = _headers(response.headers)
                try:
                    body = response.text()[:MAX_BODY]
                except Exception as e:
                    body = f"<unreadable: {e}>"
                call["response"] = _redact(body, self.secrets)
                return

    def write(self, label: str) -> Path:
        stamped = stamp()
        out = RECON_DIR / f"{stamped}-{label}"
        out.mkdir(parents=True, exist_ok=True)
        (out / "network.jsonl").write_text(
            "\n".join(json.dumps(call) for call in self.calls) + "\n"
        )
        (out / "summary.md").write_text(self.summary())
        return out

    def summary(self) -> str:
        lines = ["# Captured calls", ""]
        for i, call in enumerate(self.calls, 1):
            path = call["url"].split("?")[0].replace("https://uct.ac.za.libcal.com", "")
            lines.append(f"## {i}. {call['method']} {path} -> {call['status']}")
            fields = _field_names(str(call["body"]))
            if fields:
                lines.append("")
                lines.append("Request fields: " + ", ".join(fields))
            preview = str(call["response"])[:400].replace("\n", " ")
            if preview:
                lines.append("")
                lines.append(f"Response starts: `{preview}`")
            lines.append("")
        return "\n".join(lines)


def _field_names(body: str) -> list[str]:
    if not body:
        return []
    if body.lstrip().startswith("{") or body.lstrip().startswith("["):
        return ["<json body>"]
    names = []
    for part in body.split("&"):
        name = part.split("=", 1)[0]
        if name and name not in names:
            names.append(name)
    return names


def account_secrets() -> list[str]:
    """Values worth scrubbing out of a capture before it is committed."""
    values = [
        os.environ.get("UCT_USER", ""),
        os.environ.get("UCT_PASSWORD", ""),
        os.environ.get("GMAIL_TO", ""),
    ]
    user = os.environ.get("UCT_USER", "")
    if "@" in user:
        values.append(user.split("@")[0])
    return [v for v in values if v]

from uctbook.auth import ANALYTICS_PREFIXES, session_cookies


class FakeContext:
    def __init__(self, cookies):
        self._cookies = cookies

    def cookies(self):
        return self._cookies


def cookie(name, domain="uct.ac.za.libcal.com", http_only=True):
    return {"name": name, "domain": domain, "httpOnly": http_only, "value": "x"}


def test_analytics_is_not_a_session():
    """Analytics sets these on the LibCal domain before anyone signs in."""
    ctx = FakeContext([cookie(n, http_only=False) for n in ("_ga", "_gid", "_gat_gtag_UA_1")])
    assert session_cookies(ctx) == []


def test_a_patron_session_is_httponly():
    ctx = FakeContext([cookie("PHPSESSID"), cookie("_ga", http_only=False)])
    assert session_cookies(ctx) == ["PHPSESSID"]


def test_other_hosts_do_not_count():
    ctx = FakeContext([cookie("ESTSAUTH", domain="login.microsoftonline.com")])
    assert session_cookies(ctx) == []


def test_prefixes_are_prefixes_not_exact_names():
    assert "_ga" in ANALYTICS_PREFIXES
    ctx = FakeContext([cookie("_ga_TNBV6H9BQZ", http_only=False)])
    assert session_cookies(ctx) == []

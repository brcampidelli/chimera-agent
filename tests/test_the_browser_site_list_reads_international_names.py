"""Study 29, P5.2 review: the site list and a person's yes speak the browser's spelling of a name.

Chromium asks for an internationalised host in punycode — ``https://bücher.de/`` goes out as
``https://xn--bcher-kva.de/`` (seen on a real Chromium by the review). The yes was stored as
``bücher.de``, so the guard refused the request the yes was for, the agent asked again, the person
said yes again, and the page never loaded. And a list entry written as ``bücher.de`` was refused at
save time, forcing the owner to type punycode.
"""

from __future__ import annotations

from typing import Any

import pytest

from chimera.tools.browser_playwright import RequestGuard
from chimera.tools.browser_reach import BrowserReach, ascii_host, parse_sites


def _public(url: str) -> bool:
    return True


def test_a_yes_for_a_unicode_name_opens_the_punycode_request_chromium_sends() -> None:
    reach = BrowserReach(("example.com",), floor=_public, owned=frozenset)
    assert not reach.listed("https://bücher.de/")
    reach.approve("https://bücher.de/")
    assert reach.listed("https://xn--bcher-kva.de/")
    assert reach.listed("https://BÜCHER.de/katalog")

    sent: list[str] = []

    class _Session:
        def send(self, method: str, params: dict[str, Any]) -> None:
            sent.append(method)

    guard = RequestGuard(reach.permits, listed=reach.listed, cache=False)
    guard.on_paused(
        _Session(),
        {"requestId": "1", "request": {"url": "https://xn--bcher-kva.de/"}, "resourceType": "Document", "frameId": "M"},
        main_frame="M",
    )
    assert sent == ["Fetch.continueRequest"]


def test_a_list_written_in_unicode_is_kept_in_the_browsers_spelling() -> None:
    assert parse_sites("bücher.de, *.Bücher.de") == ("xn--bcher-kva.de", "*.xn--bcher-kva.de")
    reach = BrowserReach(parse_sites("*.bücher.de"), floor=_public, owned=frozenset)
    assert reach.listed("https://shop.xn--bcher-kva.de/")
    assert reach.listed("https://shop.bücher.de/")
    assert not reach.listed("https://xn--bcher-kva.de/")  # `*.` is the subdomains, as before


def test_the_spelling_is_chromiums_non_transitional_one() -> None:
    """IDNA 2003 (Python's own codec) maps ß to ss; Chromium keeps it. Matching the wrong one would
    approve a different site."""
    assert ascii_host("faß.de") == "xn--fa-hia.de"
    assert ascii_host("Example.COM.") == "example.com"
    assert ascii_host("::1") == "::1"


@pytest.mark.parametrize("bad", ["https://bücher.de", "bücher.de/path", "bücher..de", "*"])
def test_a_unicode_entry_that_is_not_a_host_is_still_refused(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_sites(bad)

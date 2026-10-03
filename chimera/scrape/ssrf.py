"""SSRF guard for the scrape/fetch tools.

The agent can be steered (by a task or an injected page) into fetching a URL. Without a guard it
could read internal services or the cloud metadata endpoint (``169.254.169.254``) and pull the
response into the model context — classic SSRF. ``check_url`` restricts schemes to http(s) and
rejects any host that resolves to a private / loopback / link-local / reserved address. Callers must
also re-check every redirect hop (a public URL can 302 to ``http://169.254.169.254/``).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse


def _resolve_ips(host: str) -> list[str]:
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


#: Carrier-grade NAT (RFC 6598). NOT reported as private by `ipaddress` on the interpreters this
#: project runs, so every attribute check below misses it — and `100.100.100.200` inside it is
#: Alibaba Cloud's instance metadata service, the same role `169.254.169.254` plays on AWS and GCP.
#: A `/10`, not a `/8`: `100.128.0.0` onwards is ordinary public space and must keep working.
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    mapped = getattr(ip, "ipv4_mapped", None) or ip
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or (isinstance(mapped, ipaddress.IPv4Address) and mapped in _CGNAT)
    )


#: A host whose last label is a number, in the WHATWG URL standard's sense ("ends in a number"):
#: decimal digits, or ``0x`` and hex digits. No real top-level domain is numeric, so such a host is
#: an IPv4 address in some spelling — and the spelling is where the check used to diverge from the
#: browser. ``ipaddress`` reads only the canonical dotted quad, so ``2130706433``, ``0177.0.0.1``,
#: ``0x7f.1`` and ``127.1`` fell through to ``getaddrinfo``, whose reading is the platform's: on
#: Windows each one fails to resolve (refused, by luck, as "could not resolve"), on glibc each one
#: is ``127.0.0.1`` (refused), and an ``inet_aton`` that read ``0177`` as decimal would have made
#: it ``177.0.0.1`` (public). Chromium reads all four as ``127.0.0.1``.
_NUMERIC_LAST_LABEL = re.compile(r"^(?:0[xX][0-9a-fA-F]*|[0-9]+)$")


def is_numeric_host(host: str) -> bool:
    """Whether ``host`` is an IPv4 address in a spelling other than the canonical dotted quad."""
    try:
        ipaddress.ip_address(host)
        return False  # canonical (or IPv6): `ipaddress` decides it like everyone else
    except ValueError:
        pass
    labels = host.split(".")
    if len(labels) > 1 and labels[-1] == "":
        labels.pop()  # one trailing dot is the root, as the standard says: `127.0.0.1.` is numeric
    return bool(_NUMERIC_LAST_LABEL.match(labels[-1]))


def check_url(url: str) -> None:
    """Raise ``ValueError`` if ``url`` is not a safe public http(s) URL (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked URL scheme {parsed.scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise ValueError("blocked URL with no host")
    if is_numeric_host(host):
        # Refused rather than decoded: no page needs an address spelled in octal or as one integer,
        # and decoding it here would be a second parser to keep in step with the browser's.
        raise ValueError(f"blocked IP address in a non-canonical spelling: {host!r}")
    try:
        candidates = [ipaddress.ip_address(host)]  # a literal IP in the URL
    except ValueError:
        candidates = [ipaddress.ip_address(ip) for ip in _resolve_ips(host)]
    if not candidates:
        raise ValueError(f"could not resolve host {host!r}")
    for ip in candidates:
        if _is_blocked(ip):
            raise ValueError(f"blocked internal address {ip} for host {host!r}")


def is_safe_url(url: str) -> bool:
    """Non-raising variant."""
    try:
        check_url(url)
        return True
    except ValueError:
        return False

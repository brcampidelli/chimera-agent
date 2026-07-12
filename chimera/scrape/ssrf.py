"""SSRF guard for the scrape/fetch tools.

The agent can be steered (by a task or an injected page) into fetching a URL. Without a guard it
could read internal services or the cloud metadata endpoint (``169.254.169.254``) and pull the
response into the model context — classic SSRF. ``check_url`` restricts schemes to http(s) and
rejects any host that resolves to a private / loopback / link-local / reserved address. Callers must
also re-check every redirect hop (a public URL can 302 to ``http://169.254.169.254/``).
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


def _resolve_ips(host: str) -> list[str]:
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except socket.gaierror:
        return []


def _is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local  # 169.254.0.0/16 — cloud metadata lives here
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def check_url(url: str) -> None:
    """Raise ``ValueError`` if ``url`` is not a safe public http(s) URL (SSRF guard)."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked URL scheme {parsed.scheme!r} (only http/https allowed)")
    host = parsed.hostname
    if not host:
        raise ValueError("blocked URL with no host")
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

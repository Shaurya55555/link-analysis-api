"""URL validation and SSRF protection."""
import ipaddress
import socket
from urllib.parse import urlparse

BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}


class UnsafeURLError(ValueError):
    """Raised when a URL is malformed or points at a private/internal target."""


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
    )


def validate_url(url: str) -> str:
    """Return a normalized URL or raise UnsafeURLError.

    Checks scheme, host, and resolves DNS so every resolved address must be public.
    Called again on every redirect hop by the extractor.
    """
    url = (url or "").strip()
    if not url:
        raise UnsafeURLError("URL is empty")
    if "://" not in url:
        url = "https://" + url
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if parsed.username or parsed.password:
        raise UnsafeURLError("Credentials in URL are not allowed")
    if host.lower() in BLOCKED_HOSTS or host.lower().endswith(".internal"):
        raise UnsafeURLError("Host is not allowed")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if port not in (80, 443, 8080, 8443):
        raise UnsafeURLError("Port is not allowed")

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeURLError(f"Host could not be resolved: {host}") from exc
    for info in infos:
        ip = info[4][0].split("%")[0]
        if not _is_public_ip(ip):
            raise UnsafeURLError("URL resolves to a private or internal address")
    return url

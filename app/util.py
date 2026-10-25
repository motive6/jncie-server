import ipaddress
import re

_DOMAIN_RE = re.compile(r"^(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))*\.?$", re.I)
_HOST = r"[a-z0-9][a-z0-9-]{0,62}"


def is_domain(value: str) -> bool:
    value = (value or "").strip().rstrip(".")
    return bool(value) and len(value) <= 253 and bool(_DOMAIN_RE.match(value))


def is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except ValueError:
        return False


def is_ipv4(value: str) -> bool:
    try:
        return isinstance(ipaddress.ip_address(value.strip()), ipaddress.IPv4Address)
    except (ValueError, AttributeError):
        return False


def is_network(value: str) -> bool:
    try:
        ipaddress.ip_network(value.strip(), strict=False)
        return True
    except (ValueError, AttributeError):
        return False


def split_lines(raw: str) -> list[str]:
    return [line.strip() for line in (raw or "").replace(",", "\n").splitlines() if line.strip()]

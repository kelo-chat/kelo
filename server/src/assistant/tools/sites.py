"""What "one site" is: the registrable domain of a host, by the Public Suffix List (public_suffix_list.dat, Mozilla Public License
2.0, from https://publicsuffix.org/list/public_suffix_list.dat; refresh it by downloading that file again).

The registrable domain is the public suffix plus one label: 'pay.example.co.kr' and 'www.example.co.kr' -> 'example.co.kr'. Shared hosting suffixes from the list's private section count as suffixes as well
('alice.github.io' and 'bob.github.io' are two sites). An IP address or a single-label name is its own site.

Used wherever an approval given for a site must also hold on that site's other hosts (an order on 'example.co.kr' covers its checkout
on 'pay.example.co.kr') and never on another site that only ends with the same letters ('badexample.co.kr')."""

from __future__ import annotations

import ipaddress
import threading
from pathlib import Path

_LIST = Path(__file__).with_name("public_suffix_list.dat")
_lock = threading.Lock()
_rules: tuple[frozenset[str], frozenset[str], frozenset[str]] | None = None   # (plain, wildcard parents, exceptions)
_tlds: frozenset[str] | None = None   # the top-level domains of the list's ICANN section (the delegated root zone)


def _ascii(label_path: str) -> str:
    try:
        return ".".join(p.encode("idna").decode("ascii") if p and p != "*" and not p.isascii() else p for p in label_path.split("."))
    except UnicodeError:
        return label_path


def _load() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    global _rules
    with _lock:
        if _rules is None:
            plain: set[str] = set()
            wild: set[str] = set()
            exc: set[str] = set()
            try:
                lines = _LIST.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for raw in lines:
                rule = raw.strip().split(" ", 1)[0].lower()
                if not rule or rule.startswith("//"):
                    continue
                for form in {rule, _ascii(rule)}:
                    if form.startswith("!"):
                        exc.add(form[1:])
                    elif form.startswith("*."):
                        wild.add(form[2:])
                    else:
                        plain.add(form)
            _rules = (frozenset(plain), frozenset(wild), frozenset(exc))
        return _rules


def _clean(host: str) -> str:
    h = (host or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    if ":" in h and not _is_ip(h):   # host:port
        h = h.rsplit(":", 1)[0]
    return h


def _is_ip(h: str) -> bool:
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def public_suffix(host: str) -> str:
    """The public suffix of `host` ('co.kr' for 'pay.example.co.kr'; the last label when the list has no rule for it)."""
    h = _clean(host)
    if not h or _is_ip(h):
        return h
    plain, wild, exc = _load()
    labels = h.split(".")
    best = labels[-1]   # the default rule "*": the last label
    for i in range(len(labels)):
        cand = ".".join(labels[i:])
        if cand in exc:   # an exception rule: its suffix is the candidate minus its first label
            return ".".join(labels[i + 1:])
        parent = ".".join(labels[i + 1:])
        if cand in plain or (parent and parent in wild):
            best = cand
            break   # the first hit from the left is the longest
    return best


def registrable(host: str) -> str:
    """The registrable domain of `host` (the public suffix plus one label); the host itself for an IP address, a single label or a
    bare public suffix. A leading 'www.' never matters."""
    h = _clean(host)
    if not h or _is_ip(h) or "." not in h:
        return h
    suffix = public_suffix(h)
    if h == suffix:
        return h
    rest = h[: -len(suffix) - 1]
    return rest.rsplit(".", 1)[-1] + "." + suffix


def same_site(a: str, b: str) -> bool:
    """`a` and `b` are hosts of one site (the same registrable domain)."""
    ra, rb = registrable(a), registrable(b)
    return bool(ra) and ra == rb


def _root_zone() -> frozenset[str]:
    global _tlds
    with _lock:
        if _tlds is None:
            found: set[str] = set()
            inside = False
            try:
                lines = _LIST.read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = []
            for raw in lines:
                line = raw.strip()
                if line.startswith("// ===BEGIN ICANN"):
                    inside = True
                elif line.startswith("// ===END ICANN"):
                    inside = False
                elif inside and line and not line.startswith("//") and "." not in line.split(" ", 1)[0] and not line.startswith(("*", "!")):
                    rule = line.split(" ", 1)[0].lower()
                    found.update({rule, _ascii(rule)})
            _tlds = frozenset(found - {"arpa"})   # .arpa is infrastructure (home.arpa is the special-use name of a home network)
        return _tlds


def in_public_dns(host: str) -> bool:
    """The name can exist in the public DNS: more than one label and a top-level domain delegated in the root zone (the Public Suffix
    List's ICANN section). Names a home or office network invents ('printer.lan', 'nas.local', 'wiki.corp', '.internal', 'home.arpa')
    and a single label ('freellmapi', a Docker service) are not. An IP address is not a name."""
    h = _clean(host)
    if not h or _is_ip(h) or "." not in h:
        return False
    return h.rsplit(".", 1)[-1] in _root_zone()

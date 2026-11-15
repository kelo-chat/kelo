import httpx
import pytest

from assistant import netguard
from assistant.netguard import NetGuardError, fetch, is_public, valid_host

PUBLIC = [(2, 1, 6, "", ("93.184.216.34", 443))]


def resolver_for(*ips):
    return lambda host, port, *a: [(2, 1, 6, "", (ip, port)) for ip in ips]


@pytest.mark.parametrize("ip,ok", [("93.184.216.34", True), ("2606:2800:220:1:248:1893:25c8:1946", True), ("127.0.0.1", False), ("10.0.0.5", False),
                                   ("192.168.1.1", False), ("172.16.0.1", False), ("169.254.169.254", False), ("100.64.0.1", False),
                                   ("0.0.0.0", False), ("::1", False), ("fe80::1", False), ("224.0.0.1", False), ("::ffff:10.0.0.1", False),
                                   ("not-an-ip", False)])
def test_only_public_addresses_pass(ip, ok):
    assert is_public(ip) is ok


@pytest.mark.parametrize("bad", ["localhost", "127.0.0.1", "10.0.0.1", "api", "https://api.example.com", "api.example.com/v1", "api.example.com:8443",
                                 "*.example.com", "nas.local", "svc.internal", "", " ", "a..b.com", "-a.example.com", "exa mple.com"])
def test_registered_hosts_must_be_bare_public_dns_names(bad):
    with pytest.raises(NetGuardError):
        valid_host(bad)


def test_a_good_host_is_normalized():
    assert valid_host(" API.Example.COM ") == "api.example.com"


def mock(handler):
    return httpx.MockTransport(handler)


async def test_the_request_goes_to_the_checked_address_with_the_real_host_name():
    seen = {}

    def handler(req):
        seen.update(url=str(req.url), host=req.headers["host"], auth=req.headers.get("authorization"), sni=req.extensions.get("sni_hostname"))
        return httpx.Response(200, json={"ok": True})

    r = await fetch("GET", "https://api.example.com/v1/items?limit=2", allowed_hosts=["api.example.com"],
                    headers={"Authorization": "Bearer k"}, resolver=resolver_for("93.184.216.34"), transport=mock(handler))
    assert r.status == 200 and r.body == b'{"ok":true}'
    assert seen == {"url": "https://93.184.216.34/v1/items?limit=2", "host": "api.example.com", "auth": "Bearer k", "sni": "api.example.com"}


async def test_every_refusal_happens_before_anything_is_sent():
    def boom(req):
        raise AssertionError("must not send")

    cases = [
        ("http://api.example.com/x", resolver_for("93.184.216.34"), "https"),
        ("https://other.example.com/x", resolver_for("93.184.216.34"), "registered"),
        ("https://user:pw@api.example.com/x", resolver_for("93.184.216.34"), "credentials in the URL"),
        ("https://api.example.com:8443/x", resolver_for("93.184.216.34"), "port"),
        ("https://api.example.com/x", resolver_for("10.0.0.5"), "non-public"),
        ("https://api.example.com/x", resolver_for("93.184.216.34", "169.254.169.254"), "non-public"),   # one bad answer is enough
        ("https://api.example.com/x", resolver_for("127.0.0.1"), "non-public"),
    ]
    for url, res, text in cases:
        with pytest.raises(NetGuardError, match=text):
            await fetch("GET", url, allowed_hosts=["api.example.com"], resolver=res, transport=mock(boom))

    def failing(*a):
        raise OSError("no dns")
    with pytest.raises(NetGuardError, match="could not resolve"):
        await fetch("GET", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=failing, transport=mock(boom))


async def test_redirects_are_not_followed_and_say_so():
    seen = []

    def handler(req):
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "https://evil.example.net/steal"})

    with pytest.raises(NetGuardError, match="redirected"):
        await fetch("GET", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=resolver_for("93.184.216.34"),
                    transport=mock(handler), headers={"Authorization": "Bearer k"})
    assert len(seen) == 1                                    # the credential was never sent anywhere else


async def test_big_bodies_are_cut_and_timeouts_are_clear():
    r = await fetch("GET", "https://api.example.com/big", allowed_hosts=["api.example.com"], resolver=resolver_for("93.184.216.34"),
                    transport=mock(lambda req: httpx.Response(200, content=b"x" * 5000)), max_body=1000)
    assert r.truncated and len(r.body) == 1000

    def slow(req):
        raise httpx.ReadTimeout("slow")
    with pytest.raises(NetGuardError, match="timed out"):
        await fetch("POST", "https://api.example.com/x", allowed_hosts=["api.example.com"], resolver=resolver_for("93.184.216.34"), transport=mock(slow))


async def test_hop_headers_from_the_caller_cannot_override_the_host():
    seen = {}

    def handler(req):
        seen["host"] = req.headers["host"]
        return httpx.Response(200)

    await fetch("GET", "https://api.example.com/", allowed_hosts=["api.example.com"], headers={"Host": "evil.example.net"},
                resolver=resolver_for("93.184.216.34"), transport=mock(handler))
    assert seen["host"] == "api.example.com"

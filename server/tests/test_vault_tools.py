

def test_one_service_under_two_domains_shares_its_login():
    """threads.net redirects to threads.com, twitter.com to x.com: one login, one fill-in card (owner 2026-11-14)."""
    from assistant.tools.vault_tools import login_site_ok, service_sites
    assert login_site_ok("www.threads.com", ["www.threads.net"]) and login_site_ok("x.com", ["twitter.com"])
    assert service_sites("www.threads.net") == frozenset({"threads.net", "threads.com"})
    assert not login_site_ok("threads.com", ["instagram.com"]) and not login_site_ok("evil.com", ["x.com"])

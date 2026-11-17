"""add_service_key sending a stored key to other hosts (tools/connect_tools.py, audit F11): a key that is stored is still stored when the
vault is locked (its value is not readable then), so the move asks, and while the vault is locked the move is refused outright."""

from assistant.store import Store

from test_connect import Fake, reply, tools_for

SECRET = "sk-test-0123456789abcdef"
MOVE = {"name": "acme", "hosts": ["api.evil.com"], "auth": {"type": "http", "scheme": "bearer"}}
SAME = {"name": "acme", "hosts": ["api.acme.com"], "auth": {"type": "http", "scheme": "bearer"}}


def stored(st, keyring=False):
    st.save_credential("acme", "Acme", ["api.acme.com"], {"type": "bearer_header"})
    assert st.set_credential_secret("acme", SECRET)
    if keyring:
        assert st.credential_to_keyring("acme", SECRET)
    return st


def test_a_stored_key_moved_to_other_hosts_asks_while_the_vault_is_open():
    t = tools_for(stored(Store()), Fake(reply("{}")))
    assert t["add_service_key"].validate(MOVE) is None
    assert t["add_service_key"].auto_approve(MOVE) is False                 # a card: the saved key would be dropped
    assert t["add_service_key"].auto_approve(SAME) is True                  # the same hosts: no card


def test_a_locked_vault_with_a_stored_key_refuses_the_move():
    st = stored(Store())
    st.set_vault_locked(True)
    assert st.credential("acme")["has_secret"] is True
    t = tools_for(st, Fake(reply("{}")))
    why = t["add_service_key"].validate(MOVE)
    assert why and "vault is locked" in why
    assert t["add_service_key"].validate(SAME) is None                      # nothing is moved: the call goes on as before


def test_a_keychain_key_counts_as_stored_while_locked_too():
    st = stored(Store(), keyring=True)
    st.set_vault_locked(True)
    t = tools_for(st, Fake(reply("{}")))
    assert "vault is locked" in t["add_service_key"].validate(MOVE)
    st.set_vault_locked(False)
    assert t["add_service_key"].validate(MOVE) is None
    assert t["add_service_key"].auto_approve(MOVE) is False


def test_a_new_service_without_a_stored_key_is_not_refused_when_locked():
    st = Store()
    st.set_vault_locked(True)
    t = tools_for(st, Fake(reply("{}")))
    assert t["add_service_key"].validate(MOVE) is None
    assert t["add_service_key"].auto_approve(MOVE) is True

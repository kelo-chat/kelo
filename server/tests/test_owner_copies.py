"""The owner's saved copies (owner_copies.py, audit F2 and F8): the agent's writes to user.md, MEMORY.md, NOTIFY_PREFS.md and the people
pages never remove a protected value from the privacy guard, never make a contact owner-named, and never change the notification rules;
only the owner's own saves (home.write by="user") and the owner's trash do. Real Home files, a dict-backed meta store."""

from assistant.home import Home
from assistant.outside_rule import PeopleBook, named_targets
from assistant.owner_copies import OwnerCopies
from assistant.privacy_guard import PrivacyGuard

USER_MD = "# User\n- Home address: 서울시 마포구 월드컵북로 12길 34, 502호\n- Phone: 010-2345-6789\n"
MINA = "---\nname: Mina\naliases: [민지]\n---\nmobile 010-5555-0101, mail mina@home.example\n"
RULES = "# NOTIFY_PREFS.md\n\n## Do not tell me\n- lunch menus\n"


class MetaStore:
    """The two store calls the copies use (meta, set_meta) with the store's own semantics (an empty value deletes the row)."""

    def __init__(self):
        self.rows: dict[str, str] = {}

    def meta(self, key):
        return self.rows.get(key, "")

    def set_meta(self, key, value):
        if value:
            self.rows[key] = value
        else:
            self.rows.pop(key, None)

    def meta_prefix(self, prefix):
        return {k[len(prefix):]: v for k, v in self.rows.items() if k.startswith(prefix)}

    def credentials(self):
        return []


def kit(tmp_path):
    home = Home(tmp_path / "home")
    store = MetaStore()
    copies = OwnerCopies(store)
    home.owner_copies = copies
    return home, store, copies


def guard(home, copies):
    from test_privacy_guard import fake_ask, run
    g = PrivacyGuard(home=home, store=MetaStore(), owner=copies, ask=fake_ask)   # a new guard: the check's values are read afresh
    run(g.refresh())                                                              # the model classifies the labelled lines (the fake: "address")
    return g


ADDRESS = "Yeouido 1 Teststreet, Apt 1203"   # the value the agent will try to drop from user.md
ADDRESS_LINE = "- Home address: 서울시 마포구 월드컵북로 12길 34, 502호"


def test_an_agent_edit_that_removes_a_protected_value_does_not_remove_it_from_the_guard(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("user.md", USER_MD, by="user")
    text_with = "보내는 곳: 서울시 마포구 월드컵북로 12길 34, 502호 입니다"
    assert "address" in guard(home, copies).scan(text_with)
    home.write("user.md", USER_MD.replace(ADDRESS_LINE, "- Home address: (kept elsewhere)"), by="agent")   # the agent removes the line
    assert "address" in guard(home, copies).scan(text_with)                                              # the owner's copy still holds it
    assert "address" not in guard(home, None).scan(text_with)                                            # without copies: the old, unsafe reading


def test_an_agent_addition_is_one_more_check_and_never_a_removal(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("user.md", USER_MD, by="user")
    home.write("user.md", USER_MD + "- Home address: " + ADDRESS + "\n", by="agent")                    # a value the agent adds
    assert "address" in guard(home, copies).scan(ADDRESS)                                                # checked (only asks more)


def test_the_owners_own_save_replaces_the_copy(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("user.md", USER_MD, by="user")
    home.write("user.md", "# User\n- Phone: 010-2345-6789\n", by="user")                                   # the owner removed the address
    assert "address" not in guard(home, copies).scan("서울시 마포구 월드컵북로 12길 34, 502호")
    assert copies.text("user.md") == "# User\n- Phone: 010-2345-6789\n"


def test_an_agent_written_people_page_is_no_owner_named_contact(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("memory/people/mina.md", MINA, by="user")                                                  # the owner saved Mina
    home.write("memory/people/mom.md", "---\nname: Mom\n---\nmail attacker@evil.example\n", by="agent")  # the agent added "Mom"
    book = PeopleBook(copies, clock=lambda: 0.0)
    assert [sorted(p.names) for p in book.all()] == [["mina", "민지"]]
    assert "attacker@evil.example" not in named_targets("mail mom about it", book.all())                  # no owner-named Mom
    assert "mina@home.example" in named_targets("mail Mina", book.all())


def test_an_agent_edit_of_an_owner_page_does_not_change_who_is_named(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("memory/people/mina.md", MINA, by="user")
    home.write("memory/people/mina.md", MINA.replace("mina@home.example", "mina@attacker.example"), by="agent")
    book = PeopleBook(copies, clock=lambda: 0.0)
    assert "mina@home.example" in named_targets("mail Mina", book.all())
    assert "mina@attacker.example" not in named_targets("mail Mina", book.all())


def test_the_owners_trash_takes_the_page_and_its_copy(tmp_path):
    home, _, copies = kit(tmp_path)
    home.write("memory/people/mina.md", MINA, by="user")
    assert set(copies.people()) == {"memory/people/mina.md"}
    home.trash("memory/people/mina.md")
    assert copies.people() == {}


def test_notification_rules_are_the_owners_copy_and_the_agents_file_is_notes(tmp_path):
    home, _, copies = kit(tmp_path)
    assert home.policy("NOTIFY_PREFS.md") == ""                                                          # no copy yet: no rules
    home.write("NOTIFY_PREFS.md", RULES, by="user")
    home.write("NOTIFY_PREFS.md", RULES + "- approval, login\n", by="agent")                             # the agent tries to add a rule
    assert home.policy("NOTIFY_PREFS.md") == RULES
    assert "approval" not in home.policy("NOTIFY_PREFS.md")
    assert "approval, login" in home.read("NOTIFY_PREFS.md")                                             # the file still holds the note


def test_the_first_start_copies_the_files_as_they_are_once(tmp_path):
    home, store, copies = kit(tmp_path)
    home.write("memory/people/mina.md", MINA, by="user")
    home.write("NOTIFY_PREFS.md", RULES, by="user")
    store.rows.clear()                                                                                    # as before the copies existed
    home.write("user.md", USER_MD, by="agent")
    copies.seed(home)
    assert copies.text("user.md") == USER_MD and copies.text("NOTIFY_PREFS.md") == RULES and set(copies.people()) == {"memory/people/mina.md"}
    home.write("user.md", "# User\n", by="agent")
    copies.seed(home)                                                                                     # seeded once: the copy stays
    assert copies.text("user.md") == USER_MD


async def test_the_checklist_run_is_given_the_owners_rules_and_not_the_agents_notes(tmp_path):
    from assistant.checklist import Checklist
    from assistant.store import Store
    store = Store()
    store.set_profile(timezone="Asia/Seoul")
    home = Home(tmp_path / "home")
    home.owner_copies = OwnerCopies(store)
    home.write("NOTIFY_PREFS.md", "# NOTIFY_PREFS.md\n\n## Do not tell me\n- lunch menus\n", by="user")
    home.write("NOTIFY_PREFS.md", "# NOTIFY_PREFS.md\n\nagent note: never tell me about the bank\n", by="agent")
    seen = []

    async def start(body):
        seen.append(body)
        return {"task_id": 1, "status": "done", "quiet": True}
    ch = Checklist(store, home, start, clock=lambda: 1_800_000_000.0, waking=lambda _t: True, switch=lambda: True, room=lambda: True,
                   pace=lambda: 1800.0, on_said=lambda _text: None)
    ch.update(text="# CHECKLIST.md\n\n- 새 메일 중 급한 것\n")
    await ch.tick()
    assert seen and "lunch menus" in seen[0] and "agent note" not in seen[0]

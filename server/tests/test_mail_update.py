"""email_update: marking, flagging, archiving, moving and trashing messages of a connected mailbox, against pretend IMAP servers (one
that behaves like Gmail with its labels, one like iCloud, one like a server with no special-use folders). No real server is contacted.
Nothing is ever deleted for good: no EXPUNGE and no \\Deleted flag anywhere."""

import json
import re
from email.message import EmailMessage

from assistant.agent import current_call
from assistant.agent.loop import _execute
from assistant.mail import Mail, Selection, mutf7_decode, mutf7_encode, uid_sets
from assistant.providers.types import ToolCall
from assistant.schedule_grants import action_of
from assistant.store import Store
from assistant.tool_labels import ask, label
from assistant.tools.mail_tools import mail_tools
from assistant import i18n

ADDRESS = "nahyun.kim@gmail.test"
PASSWORD = "abcdefghijklmnop"
TRASH_KO = "[Gmail]/휴지통"          # a Korean Gmail account's Trash: found by its \Trash mark, sent in modified UTF-7


class Msg:
    def __init__(self, uid, subject="hello", sender="kim@example.com", seen=False, flagged=False, labels=("\\Inbox",), cat="primary"):
        self.uid, self.subject, self.sender, self.cat = uid, subject, sender, cat
        self.flags = ({"\\Seen"} if seen else set()) | ({"\\Flagged"} if flagged else set())
        self.labels = set(labels)
        self.trashed = False


def parse_set(text):
    out = set()
    for part in text.split(","):
        a, _, b = part.partition(":")
        out |= set(range(int(a), int(b or a) + 1))
    return out


class FakeImap:
    """A pretend IMAP server. gmail=True: one store of messages seen through label folders (INBOX = the \\Inbox label, All Mail = all
    not in the trash), like Gmail. Otherwise: real folders, MOVE takes a message from one to another."""

    def __init__(self, *, gmail=True, move=True, special=True, archive=True, trash_name=TRASH_KO):
        self.gmail, self.move, self.special = gmail, move, special
        self.log: list[tuple] = []
        self.selected = None
        self.readonly = None
        if gmail:
            self.msgs: dict[int, Msg] = {}
            self.folders = {"INBOX": set(), "[Gmail]/All Mail": {"\\All"}, trash_name: {"\\Trash"}, "[Gmail]/Starred": {"\\Flagged"},
                            "Receipts": set(), "영수증": set(), "[Gmail]": {"\\Noselect"}}
            self.trash = trash_name
        else:
            names = ["INBOX", "Deleted Messages", "Sent Messages", "Work"] + (["Archive"] if archive else [])
            self.boxes: dict[str, dict[int, Msg]] = {n: {} for n in names}
            self.folders = {n: set() for n in names}
            if special:
                self.folders["Deleted Messages"] = {"\\Trash"}
                if archive:
                    self.folders["Archive"] = {"\\Archive"}
            self.next_uid = 10_000

    def add(self, msg, folder="INBOX"):
        if self.gmail:
            self.msgs[msg.uid] = msg
        else:
            self.boxes[folder][msg.uid] = msg

    # ---- what a folder holds
    def view(self, folder):
        if not self.gmail:
            return self.boxes[folder]
        if folder == self.trash:
            return {u: m for u, m in self.msgs.items() if m.trashed}
        if folder == "[Gmail]/All Mail":
            return {u: m for u, m in self.msgs.items() if not m.trashed}
        want = "\\Inbox" if folder == "INBOX" else folder
        return {u: m for u, m in self.msgs.items() if not m.trashed and want in m.labels}

    def imap(self, host):
        server = self

        class Conn:
            literal = None

            def login(self, user, pw):
                assert (user, pw) == (ADDRESS, PASSWORD)
                return "OK", [b"ok"]

            def capability(self):
                caps = "IMAP4rev1 UIDPLUS" + (" X-GM-EXT-1" if server.gmail else "") + (" MOVE" if server.move else "")
                return "OK", [caps.encode()]

            def list(self, *a):
                rows = []
                for name, flags in server.folders.items():
                    raw = mutf7_encode(name)
                    shown = flags if server.special else set()
                    rows.append(f'({" ".join(sorted(shown | {"\\HasNoChildren"}))}) "/" "{raw}"'.encode())
                return "OK", rows

            def select(self, folder, readonly=False):
                name = mutf7_decode(json.loads(folder) if folder.startswith('"') else folder)
                server.log.append(("SELECT", name, readonly))
                if name not in server.folders or "\\Noselect" in server.folders[name]:
                    return "NO", [b"no such folder"]
                server.selected, server.readonly = name, readonly
                return "OK", [str(len(server.view(name))).encode()]

            def expunge(self):
                server.log.append(("EXPUNGE",))
                raise AssertionError("never expunge")

            def uid(self, cmd, *args):
                server.log.append((cmd, *args))
                box = server.view(server.selected)
                if cmd == "SEARCH":
                    return "OK", [" ".join(str(u) for u in sorted(self.search(box, list(args)))).encode()]
                if cmd == "FETCH":
                    which, spec = args
                    assert "BODY.PEEK" in spec or "BODY" not in spec, spec      # reading never marks as read
                    rows = []
                    for u in sorted(parse_set(which)):
                        if u in box:
                            m = box[u]
                            em = EmailMessage()
                            em["From"], em["Subject"] = m.sender, m.subject
                            head = em.as_bytes().split(b"\n\n", 1)[0] + b"\r\n\r\n"
                            flags = " ".join(sorted(m.flags))
                            rows += [(f"{u} (UID {u} FLAGS ({flags}) BODY[] {{{len(head)}}}".encode(), head + b"Body text.\r\n"), b")"]
                    return "OK", rows
                assert not server.readonly, f"{cmd} on a folder opened read-only"
                if cmd == "EXPUNGE":
                    raise AssertionError("never expunge")
                if cmd == "STORE":
                    which, op, value = args
                    assert "\\Deleted" not in value, "never mark for deletion"
                    items = set(re.findall(r'"[^"]*"|\\?[\w\[\]/]+', value.strip("()")))
                    items = {json.loads(i) if i.startswith('"') else i for i in items}
                    for u in parse_set(which) & set(box):
                        m = box[u]
                        if op.endswith("X-GM-LABELS"):
                            assert server.gmail
                            names = {mutf7_decode(i) for i in items}
                            m.labels = m.labels | names if op.startswith("+") else m.labels - names
                        elif op.startswith("+"):
                            m.flags |= items
                        else:
                            m.flags -= items
                    return "OK", [None]
                if cmd == "MOVE":
                    assert server.move
                    which, dest = args
                    dest = mutf7_decode(json.loads(dest))
                    assert dest in server.folders
                    for u in sorted(parse_set(which) & set(box)):
                        m = box[u]
                        if server.gmail:
                            if dest == server.trash:
                                m.trashed = True
                            else:
                                if server.selected == "INBOX":
                                    m.labels.discard("\\Inbox")
                                elif server.selected in m.labels:
                                    m.labels.discard(server.selected)
                                m.labels.add("\\Inbox" if dest == "INBOX" else dest)
                        else:
                            del server.boxes[server.selected][u]
                            server.next_uid += 1
                            server.boxes[dest][server.next_uid] = m
                    return "OK", [None]
                raise AssertionError(f"unexpected UID {cmd}")

            def search(self, box, args):
                found = set(box)
                if args[:2] == ["CHARSET", "UTF-8"]:   # the search word follows as a literal, right after its key
                    args = args[2:] + [self.literal.decode()]
                    self.literal = None
                i = 0
                while i < len(args):
                    a = args[i]
                    if a == "ALL":
                        pass
                    elif a in ("UNSEEN", "SEEN"):
                        found = {u for u in found if ("\\Seen" in box[u].flags) == (a == "SEEN")}
                    elif a in ("FLAGGED", "UNFLAGGED"):
                        found = {u for u in found if ("\\Flagged" in box[u].flags) == (a == "FLAGGED")}
                    elif a == "UID":
                        i += 1
                        found &= parse_set(args[i])
                    elif a == "X-GM-LABELS":
                        assert server.gmail
                        i += 1
                        found = {u for u in found if args[i] in box[u].labels}
                    elif a in ("X-GM-RAW", "TEXT"):
                        assert (a == "X-GM-RAW") == server.gmail
                        i += 1
                        q = args[i][1:-1] if args[i].startswith('"') else args[i]
                        if q.startswith("category:"):
                            found = {u for u in found if box[u].cat == q[9:]}
                        else:
                            found = {u for u in found if q in box[u].subject or q in box[u].sender}
                    else:
                        raise AssertionError(f"unexpected search key {a}")
                    i += 1
                return found

            def logout(self):
                return "BYE", []
        return Conn()

    def commands(self, name):
        return [c for c in self.log if c[0] == name]


async def connected(tmp_path, fake, provider="gmail"):
    st = Store(tmp_path / "s.db")
    mail = Mail(st, imap=fake.imap, smtp=None)
    st.save_credential(f"mail-{provider}", f"Gmail · {ADDRESS}", ["imap.gmail.com"], {"type": "mailbox"}, "", kind="email")
    st.set_credential_secret(f"mail-{provider}", json.dumps({"u": ADDRESS, "p": PASSWORD}))
    tools = {t.name: t for t in mail_tools(mail)}
    return st, mail, tools


def no_deletion(fake):
    assert not fake.commands("EXPUNGE") and not any("\\Deleted" in " ".join(map(str, c)) for c in fake.log)


def test_uid_sets_are_compact_chunks():
    assert uid_sets([5, 1, 2, 3, 9, 10, 12]) == [("1:3,5,9:10,12", 7)]
    chunks = uid_sets(list(range(1, 1201)), size=500)
    assert chunks == [("1:500", 500), ("501:1000", 500), ("1001:1200", 200)]
    assert mutf7_encode("[Gmail]/휴지통") == "[Gmail]/&1zTJwNG1-" and mutf7_decode("[Gmail]/&1zTJwNG1-") == "[Gmail]/휴지통"
    assert mutf7_encode("A&B") == "A&-B" and mutf7_decode("A&-B") == "A&B" and mutf7_decode(mutf7_encode("영수증 2026")) == "영수증 2026"


async def test_each_flag_action_and_the_counts(tmp_path):
    fake = FakeImap()
    for u in range(1, 6):
        fake.add(Msg(u, seen=u <= 2, flagged=u == 5))
    _, _, tools = await connected(tmp_path, fake)
    upd = tools["email_update"].handler
    out = await upd({"action": "read", "uids": ["1", "3", "4"]})
    assert out == "Marked 2 messages as read in INBOX. 1 was already read."
    assert all("\\Seen" in fake.msgs[u].flags for u in (1, 3, 4)) and "\\Seen" not in fake.msgs[5].flags
    assert (await upd({"action": "unread", "uids": ["3"]})) == "Marked 1 message as unread in INBOX."
    assert "\\Seen" not in fake.msgs[3].flags
    assert (await upd({"action": "flag", "uids": ["1", "2"]})).startswith("Flagged 2 messages in INBOX")
    assert {"\\Flagged"} <= fake.msgs[1].flags and {"\\Flagged"} <= fake.msgs[2].flags
    assert (await upd({"action": "unflag", "uids": ["5"]})) == "Unflagged 1 message in INBOX."
    assert "\\Flagged" not in fake.msgs[5].flags
    assert (await upd({"action": "read", "uids": ["1"]})) == "That message was already read; nothing was changed."
    assert (await upd({"action": "read", "uids": ["1", "2"]})) == "All 2 messages were already read; nothing was changed."
    # every change opened the folder read-write, each STORE was one command for the whole set
    stores = fake.commands("STORE")
    assert stores[0] == ("STORE", "3:4", "+FLAGS.SILENT", "(\\Seen)")
    assert ("SELECT", "INBOX", False) in fake.log
    # nothing chosen at all is refused rather than taken as "everything"
    assert (await upd({"action": "read"})).startswith("error: say which messages")
    assert (await upd({"action": "read", "uids": ["1; DROP"]})).startswith("error: uids must be")
    assert (await upd({"action": "delete", "uids": ["1"]})).startswith("error: action must be one of")
    no_deletion(fake)


async def test_marking_thousands_read_is_one_search_and_chunked_stores(tmp_path):
    fake = FakeImap()
    for u in range(1, 2346):
        fake.add(Msg(u, seen=u % 7 == 0))
    unread = sum(1 for m in fake.msgs.values() if "\\Seen" not in m.flags)
    _, _, tools = await connected(tmp_path, fake)
    out = await tools["email_update"].handler({"action": "read", "unread": True})
    assert out == f"Marked {unread:,} messages as read in INBOX."
    assert all("\\Seen" in m.flags for m in fake.msgs.values())
    assert len(fake.commands("SEARCH")) == 1                                    # one search, never one call per message
    stores = fake.commands("STORE")
    assert len(stores) == -(-unread // 500) and all(s[2] == "+FLAGS.SILENT" for s in stores)
    assert sum(len(parse_set(s[1])) for s in stores) == unread
    assert not fake.commands("FETCH")
    no_deletion(fake)


async def test_a_server_that_stops_midway_reports_the_real_count(tmp_path):
    fake = FakeImap()
    for u in range(1, 1201):
        fake.add(Msg(u))
    real = fake.imap

    def flaky(host):
        conn = real(host)
        orig = conn.uid
        stores = [0]

        def uid(cmd, *args):
            if cmd == "STORE":
                stores[0] += 1
                if stores[0] == 3:
                    return "NO", [b"[LIMIT] too many commands"]
            return orig(cmd, *args)
        conn.uid = uid
        return conn
    fake.imap = flaky
    _, _, tools = await connected(tmp_path, fake)
    out = await tools["email_update"].handler({"action": "read", "all": True})
    assert out.startswith("Marked 1,000 messages as read in INBOX.") and "stopped after 1,000 of 1,200" in out and "too many commands" in out
    assert sum("\\Seen" in m.flags for m in fake.msgs.values()) == 1000


async def test_gmail_search_words_and_promotions(tmp_path):
    fake = FakeImap()
    for u in range(1, 31):
        fake.add(Msg(u, cat="promotions" if u % 3 == 0 else "primary", subject=f"쿠폰 {u}" if u % 3 == 0 else f"hello {u}"))
    _, _, tools = await connected(tmp_path, fake)
    out = await tools["email_update"].handler({"action": "archive", "query": "category:promotions"})
    assert out == "Archived 10 messages (the Inbox label taken off; they stay in All Mail)."
    assert fake.commands("SEARCH")[0] == ("SEARCH", "X-GM-RAW", '"category:promotions"')
    # Gmail archive = the Inbox label taken off: they stay in All Mail, nothing moved to a folder
    assert all(("\\Inbox" in m.labels) == (m.cat != "promotions") for m in fake.msgs.values())
    assert not fake.commands("MOVE") and all(c[2] == "-X-GM-LABELS" and c[3] == "(\\Inbox)" for c in fake.commands("STORE"))
    assert len(fake.view("[Gmail]/All Mail")) == 30
    # a Korean search word goes as a UTF-8 literal
    out = await tools["email_update"].handler({"action": "read", "folder": "[Gmail]/All Mail", "query": "쿠폰"})
    assert out == "Marked 10 messages as read in [Gmail]/All Mail."
    assert fake.commands("SEARCH")[-1][:3] == ("SEARCH", "CHARSET", "UTF-8")
    # archiving from All Mail only counts the ones that still have the Inbox label
    out = await tools["email_update"].handler({"action": "archive", "folder": "[Gmail]/All Mail", "uids": ["1", "3"]})
    assert out == "Archived 1 message (the Inbox label taken off in [Gmail]/All Mail; they stay in All Mail). 1 was already archived."
    assert "\\Inbox" not in fake.msgs[1].labels
    no_deletion(fake)


async def test_gmail_move_to_a_label_and_back(tmp_path):
    fake = FakeImap()
    for u in range(1, 4):
        fake.add(Msg(u))
    _, _, tools = await connected(tmp_path, fake)
    upd = tools["email_update"].handler
    assert (await upd({"action": "move", "uids": ["1", "2"], "to": "영수증"})) == "Moved 2 messages from INBOX to 영수증."
    assert fake.commands("MOVE")[-1] == ("MOVE", "1:2", '"&xgHCGMmd-"')                 # the label in modified UTF-7
    assert fake.msgs[1].labels == {"영수증"} and "\\Inbox" in fake.msgs[3].labels
    # from All Mail the label is added (nothing leaves All Mail except to the trash)
    out = await upd({"action": "move", "folder": "[Gmail]/All Mail", "uids": ["1"], "to": "INBOX"})
    assert out == "Moved 1 message (label INBOX added; All Mail keeps every message)."
    assert fake.msgs[1].labels == {"영수증", "\\Inbox"}
    assert (await upd({"action": "move", "uids": ["3"], "to": "Nowhere"})).startswith("error: there is no folder or label 'Nowhere' (none is created)")
    assert "Receipts" in (await upd({"action": "move", "uids": ["3"], "to": "Nowhere"}))
    assert (await upd({"action": "move", "uids": ["3"], "to": "receipts"})) == "Moved 1 message from INBOX to Receipts."   # any case
    assert (await upd({"action": "read", "folder": "Nope", "all": True})).startswith("error: there is no folder 'Nope'")
    no_deletion(fake)


async def test_trash_goes_to_the_trash_folder_and_is_never_expunged(tmp_path):
    fake = FakeImap()
    for u in range(1, 1301):
        fake.add(Msg(u, sender="news@shop.example" if u <= 1200 else "kim@example.com"))
    _, _, tools = await connected(tmp_path, fake)
    out = await tools["email_update"].handler({"action": "trash", "query": "news@shop.example"})
    assert out == f"Moved 1,200 messages from INBOX to the trash ({TRASH_KO}). They can be restored from there."
    moves = fake.commands("MOVE")
    assert len(moves) == 3 and all(m[2] == '"[Gmail]/&1zTJwNG1-"' for m in moves)          # found by its \Trash mark
    assert len(fake.view(TRASH_KO)) == 1200 and len(fake.view("INBOX")) == 100 and len(fake.msgs) == 1300   # restorable: all still there
    assert (await tools["email_update"].handler({"action": "trash", "folder": TRASH_KO, "all": True})).startswith("error: these messages are already in")
    no_deletion(fake)


async def test_other_providers_archive_and_trash_by_folder(tmp_path):
    fake = FakeImap(gmail=False)
    for u in range(1, 5):
        fake.add(Msg(u))
    _, _, tools = await connected(tmp_path, fake, "icloud")
    upd = tools["email_update"].handler
    assert (await upd({"action": "archive", "uids": ["1", "2"]})) == "Archived 2 messages from INBOX to Archive."
    assert len(fake.boxes["Archive"]) == 2 and set(fake.boxes["INBOX"]) == {3, 4}
    assert (await upd({"action": "trash", "uids": ["3"]})).startswith("Moved 1 message from INBOX to the trash (Deleted Messages)")
    assert (await upd({"action": "move", "uids": ["4"], "to": "work"})) == "Moved 1 message from INBOX to Work."
    assert fake.commands("SEARCH")[0] == ("SEARCH", "UID", "1:2")                        # no Gmail words on another server
    no_deletion(fake)
    # no Archive folder: said plainly, nothing moved; no special-use marks: no guess from folder names, the folders are listed
    bare = FakeImap(gmail=False, special=False, archive=False)
    bare.add(Msg(1))
    (tmp_path / "b").mkdir()
    _, _, tools = await connected(tmp_path / "b", bare, "naver")
    out = await tools["email_update"].handler({"action": "archive", "uids": ["1"]})
    assert out.startswith("error: this mailbox has no Archive folder, so archiving is not available here") and "Work" in out
    out = await tools["email_update"].handler({"action": "trash", "uids": ["1"]})
    assert out.startswith("error: this mailbox marks no Trash folder, so nothing was moved") and "Deleted Messages" in out
    assert (await tools["email_update"].handler({"action": "move", "uids": ["1"], "to": "Deleted Messages"})).startswith("Moved 1 message")
    no_deletion(bare)
    # a server without MOVE: refused, never emulated with a delete
    old = FakeImap(gmail=False, move=False)
    old.add(Msg(1))
    (tmp_path / "c").mkdir()
    _, _, tools = await connected(tmp_path / "c", old, "daum")
    assert (await tools["email_update"].handler({"action": "trash", "uids": ["1"]})).startswith("error: this mail server cannot move messages")
    assert (await tools["email_update"].handler({"action": "read", "uids": ["1"]})) == "Marked 1 message as read in INBOX."
    assert not old.commands("COPY") and not old.commands("MOVE")
    no_deletion(old)


async def test_only_trash_asks_and_only_once_per_batch(tmp_path):
    fake = FakeImap()
    for u in range(1, 1501):
        fake.add(Msg(u, subject=f"Sale {u}", cat="promotions"))
    _, _, tools = await connected(tmp_path, fake)
    tool = tools["email_update"]
    asked = []

    async def approve(req):
        asked.append(req)
        return True
    by_name = {"email_update": tool}
    for action in ("read", "unread", "flag", "unflag", "archive"):
        out, err, approved, _ = await _execute(ToolCall(f"c-{action}", "email_update", json.dumps({"action": action, "query": "category:promotions"})),
                                               by_name, approve)
        assert not err and approved is None, out
    out, err, approved, _ = await _execute(ToolCall("c-move", "email_update", json.dumps({"action": "move", "folder": "[Gmail]/All Mail",
                                                                                           "uids": ["1"], "to": "Receipts"})), by_name, approve)
    assert not err and approved is None and asked == []                                  # easy to undo: no card at all
    token = current_call.set("call-trash")
    try:
        out, err, approved, _ = await _execute(ToolCall("call-trash", "email_update", json.dumps({"action": "Trash", "query": "category:promotions",
                                                                                                  "folder": "[Gmail]/All Mail"})), by_name, approve)
    finally:
        current_call.reset(token)
    assert not err and approved is True and len(asked) == 1, out                        # one card for 1,500 messages
    req = asked[0]
    assert req.detail.startswith("1,500 messages in [Gmail]/All Mail") and "category:promotions" in req.detail
    assert req.arguments["account"] == "gmail"                                         # filled in, so a task's permission can be checked
    assert out == f"Moved 1,500 messages from [Gmail]/All Mail to the trash ({TRASH_KO}). They can be restored from there."
    assert ask("email_update") == "Move these messages to the trash?" and label("email_update") == "Organizing mail"
    tok = i18n.use("ko")
    try:
        assert label("email_update") == "메일 정리 중" and ask("email_update") == "이 메일들을 휴지통으로 옮길까요?"
    finally:
        i18n.reset(tok)
    no_deletion(fake)


async def test_trash_does_only_what_the_card_showed(tmp_path):
    fake = FakeImap()
    for u in range(1, 4):
        fake.add(Msg(u, subject=f"Invoice {u}", sender="billing@example.com"))
    _, _, tools = await connected(tmp_path, fake)
    tool = tools["email_update"]
    seen = []

    async def approve(req):
        seen.append(req.detail)
        fake.add(Msg(99, subject="Invoice 99", sender="billing@example.com"))   # arrives while the card is up
        return True
    out, err, _, _ = await _execute(ToolCall("t1", "email_update", json.dumps({"action": "trash", "query": "billing@example.com"})),
                                    {"email_update": tool}, approve)
    assert seen == ["3 messages in INBOX: Invoice 1 · Invoice 2 · Invoice 3 · Search: billing@example.com"]
    assert out.startswith("Moved 3 messages") and not fake.msgs[99].trashed               # the newcomer was not on the card
    # declined: nothing moved
    async def no(req):
        return False
    out, err, approved, _ = await _execute(ToolCall("t2", "email_update", json.dumps({"action": "trash", "uids": ["99"]})), {"email_update": tool}, no)
    assert err and approved is False and "Nothing was moved" in out and not fake.msgs[99].trashed
    # nothing matched: said without a card
    out, err, approved, _ = await _execute(ToolCall("t3", "email_update", json.dumps({"action": "trash", "query": "nobody@x"})), {"email_update": tool}, no)
    assert approved is None and "nothing was moved" in out
    no_deletion(fake)


def test_a_task_permission_covers_trash_only_as_a_deleting_change():
    assert action_of("email_update", {"action": "read", "all": True}) is None
    assert action_of("email_update", {"action": "trash", "account": "gmail"}) == ("change", ["connector:gmail"], True, "")
    assert action_of("email_update", {"action": "trash", "account": "mail-naver"}) == ("change", ["connector:naver"], True, "")


async def test_reading_still_peeks(tmp_path):
    fake = FakeImap()
    fake.add(Msg(1, subject="회의"))
    _, _, tools = await connected(tmp_path, fake)
    assert "회의" in await tools["email_list"].handler({})
    assert "Body text." in await tools["email_read"].handler({"uid": "1"})
    fetches = fake.commands("FETCH")
    assert fetches and all("BODY.PEEK[" in f[2] and "BODY[" not in f[2].replace("BODY.PEEK[", "") for f in fetches)
    assert all(c[2] is True for c in fake.commands("SELECT"))                             # opened read-only
    assert "\\Seen" not in fake.msgs[1].flags
    assert Selection.of(uids=["2", "1", "3"]).criteria() == ["UID", "1:3"]

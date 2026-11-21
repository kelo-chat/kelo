"""Tests for shopping product results: verification and persistence (card p-shopping-like-reference)."""

import hashlib
from assistant.agent.fence import fence
from assistant.shopping import ID_RE, Product, ProductResults, amounts, page_key, pages_read, verify
from assistant.store import Store


def test_a_product_seen_on_an_opened_page_with_its_price_is_kept():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1?x=2\nTitle: Gentle wash\n...가격 12,900원 ... 구매하기",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Gentle wash 500ml",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "in_stock",
            "image_url": "https://img.shop.example/1.jpg",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not left
    assert len(kept) == 1
    p = kept[0]
    assert p.name == "Gentle wash 500ml"
    assert p.url == "https://shop.example/p/1"
    assert p.seller == "shop.example"
    assert p.price == "12,900원"
    assert p.image_url == "https://img.shop.example/1.jpg"
    assert ID_RE.match(p.id)


def test_a_product_from_a_page_the_job_never_opened_is_left_out():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1\nTitle: Gentle wash\n...가격 12,900원 ... 구매하기",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Blog wash",
            "url": "https://blog.example/review-123",
            "price": "12,900원",
            "availability": "in_stock",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not kept
    assert left == ["Blog wash: not on a page you opened in this job"]


def test_a_price_the_page_does_not_show_is_left_out():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1\nTitle: Gentle wash\n...가격 15,900원 ... 구매하기",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Gentle wash 500ml",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "in_stock",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not kept
    assert left == ["Gentle wash 500ml: that price is not on its page"]


def test_out_of_stock_or_missing_fields_are_left_out():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1\nTitle: Gentle wash\n...가격 12,900원 ... 품절",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Sold out wash",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "out_of_stock",
        },
        {
            "name": "",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "in_stock",
        },
        {
            "name": "No price wash",
            "url": "https://shop.example/p/1",
            "price": "",
            "availability": "in_stock",
        },
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not kept
    assert left == [
        "Sold out wash: not shown as buyable now",
        "product: name, address or price missing",
        "No price wash: name, address or price missing",
    ]


def test_a_sale_price_not_on_the_page_is_dropped_but_the_product_stays():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1\nTitle: Gentle wash\n...정가 15,900원 ... 구매하기",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Gentle wash",
            "url": "https://shop.example/p/1",
            "price": "15,900원",
            "sale_price": "9,900원",
            "availability": "in_stock",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not left
    assert len(kept) == 1
    assert kept[0].price == "15,900원"
    assert kept[0].sale_price == ""


def test_a_non_https_picture_is_dropped():
    transcript = [
        {
            "role": "tool",
            "tool_call_id": "c1",
            "content": "URL: https://www.shop.example/p/1\nTitle: Gentle wash\n...가격 12,900원 ... 구매하기",
        }
    ]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Gentle wash",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "in_stock",
            "image_url": "http://img.shop.example/insecure.jpg",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not left
    assert len(kept) == 1
    assert kept[0].image_url == ""


def test_fenced_tool_output_is_read():
    fenced_page = fence("URL: https://www.shop.example/p/1\nTitle: Gentle wash\n가격 12,900원", source="web")
    transcript = [{"role": "tool", "tool_call_id": "c1", "content": fenced_page}]
    pages = pages_read(transcript)
    raw = [
        {
            "name": "Gentle wash",
            "url": "https://shop.example/p/1",
            "price": "12,900원",
            "availability": "in_stock",
        }
    ]
    kept, left = verify(raw, pages, "w123456")
    assert not left
    assert len(kept) == 1
    assert kept[0].price == "12,900원"


def test_results_are_stored_and_found_by_id_and_forgotten_with_their_job():
    store = Store(":memory:")
    repo = ProductResults(store)
    p1 = Product(id="p0123456789", name="Wash A", url="https://a.example/p1", seller="a.example", price="10,000원")
    p2 = Product(id="p9876543210", name="Wash B", url="https://b.example/p2", seller="b.example", price="20,000원", sale_price="18,000원", image_url="https://img.example/b.jpg")

    repo.save("w1", [p1], 1000.0)
    repo.save("w2", [p2], 1001.0)

    assert repo.get("p0123456789") == p1
    assert repo.get("p9876543210") == p2
    assert repo.get("nonexistent") is None

    assert repo.of_worker("w1") == [p1]
    assert repo.of_worker("w2") == [p2]

    repo.forget_workers({"w2"})
    assert repo.get("p0123456789") is None
    assert repo.get("p9876543210") == p2
    assert repo.of_worker("w1") == []
    assert repo.of_worker("w2") == [p2]


# ------------------------------------------------------------------ C2 worker report & handoff tests
from pathlib import Path

import httpx
from assistant.agent import Tool
from assistant.server import create_app
from assistant.tools.browser import BrowserSession
from assistant.workers import HANDOFF_MARK, Limits, WorkerHost, Workers, REPORT_REASONS
from test_agent_profile import ShotPage, app_settings
from test_server import AUTH, Running, follow
from test_workers import Brain, call, one_worker, say, until


async def _fake_browser_open(args):
    url = str(args.get("url") or "")
    if "refused" in url:
        return f"URL: {url}\nTitle: Access Denied\n403 Forbidden"
    if "p/2" in url:
        return f"URL: {url}\nTitle: Gentle wash 2\n가격 15,900원 (할인가 13,900원) ... 구매하기"
    return f"URL: {url}\nTitle: Gentle wash\n가격 12,900원 ... 구매하기"


browser_open_tool = Tool("browser_open", "Open url", {"type": "object", "properties": {"url": {"type": "string"}}}, _fake_browser_open)


def _shopping_app(tmp_path, brain, **kw):
    async def connect():
        return ShotPage("https://shop.example/cart")

    return create_app(
        app_settings(tmp_path, browser_worker=True, **kw),
        tools=[browser_open_tool],
        stream_fn=brain,
        with_worker_tools=True,
        with_goal_tools=True,
        browser=BrowserSession(connect=connect),
    )


def _make_workers(store):
    host = WorkerHost(start_turn=lambda *a: None, cancel_turn=lambda t: None, handoff=None, emit=None, tools=lambda: [])
    return Workers(store, host, Limits())


async def test_a_report_with_a_product_the_job_never_opened_is_refused_once_then_kept_without_it(tmp_path):
    seen_worker_results = []

    def chat(req, n):
        if req.messages[-1].get("role") == "tool":
            return say("delegated")
        return call("delegate", {"task": "find bodywash", "kind": "browser"})

    def worker(req, n):
        for m in req.messages:
            if m.get("role") == "tool" and m["content"] not in seen_worker_results:
                seen_worker_results.append(m["content"])
        if n == 0:
            return call("browser_open", {"url": "https://shop.example/p/1"})
        # n == 1 or 2: report with p/1 and blog address
        return call("report", {
            "status": "done",
            "summary": "found items",
            "products": [
                {"name": "Gentle wash", "url": "https://shop.example/p/1", "price": "12,900원", "availability": "in_stock"},
                {"name": "Blog wash", "url": "https://blog.example/wash", "price": "10,000원", "availability": "in_stock"},
            ],
        }, cid=f"c-report-{n}")

    handoffs = []

    def handoff(req, n):
        handoffs.append(req.messages[-1]["content"])
        return say("ok")

    app = _shopping_app(tmp_path, Brain(chat=chat, worker=worker, handoff=handoff))

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "find bodywash"})).json()["id"])
        w = await one_worker(c, "done")

    # Assert that the first report call returned the Error text to the worker
    assert any("Error: not recorded. Report only products whose own page you opened in this job" in r for r in seen_worker_results)
    assert any("Blog wash: not on a page you opened in this job" in r for r in seen_worker_results)

    # Assert product_results holds only p/1
    products = app.state.workers.products.of_worker(w["id"])
    assert len(products) == 1
    assert products[0].name == "Gentle wash"

    # Assert handoff text
    assert handoffs
    h = handoffs[0]
    assert "Products this job saw on the sellers' own pages (offer only these, by id):" in h
    assert products[0].id in h
    assert "Offer them as one choice card: quick_replies with one option per product" in h
    assert "do not list them" in h
    assert "Do not rebuild products from the summary." in h


def test_the_handoff_lists_verified_products_by_id_and_asks_for_one_choice_card(tmp_path):
    store = Store(":memory:")
    w = _make_workers(store)
    p = Product(id="p0123456789", name="Wash", url="https://shop.example/p/1", seller="shop.example", price="12,900원", sale_price="9,900원")
    w.products.save("w1", [p], 1000.0)
    rep = {"status": "done", "summary": "found", "details": "", "products": ["p0123456789"]}
    r = {"id": "w1", "title": "search", "kind": "browser", "parent_source": "chat"}
    text = w._handoff_text(r, rep, {})
    assert "Products this job saw on the sellers' own pages (offer only these, by id):" in text
    assert "- p0123456789 · Wash · 12,900원 (now 9,900원) · shop.example" in text
    assert "Offer them as one choice card: quick_replies with one option per product for the best fits, at most three (a short name as the label) and each card's product set to its id, one of them recommended." in text
    assert "In your text give one short takeaway and the decisive difference, naming at most two; do not list them." in text
    assert "Do not rebuild products from the summary." in text


def test_blocked_site_refused_handoff(tmp_path):
    store = Store(":memory:")
    w = _make_workers(store)
    rep = {"status": "blocked", "summary": "blocked", "details": "", "reason": "site_refused"}
    r = {"id": "w1", "title": "search", "kind": "browser", "parent_source": "chat", "last_url": "https://shop.example/goods"}
    text = w._handoff_text(r, rep, {})
    assert "Tell the user in one or two lines that shop.example did not let Kelo's browser in" in text
    assert "take over the browser (the live view)" in text
    assert "If no other seller could be opened either, give only the refusal and the sign-in path, no prices." in text
    assert "Never show prices, stock or links from a text search, not even as information." in text
    assert "or another shop" not in text
    assert "another seller only if the user allows it" not in text
    assert "do not offer another site's items" not in text


def test_an_unattended_blocked_job_offers_no_take_over():
    store = Store(":memory:")
    w = _make_workers(store)
    rep = {"status": "blocked", "summary": "blocked", "details": "", "reason": "site_refused"}
    r = {"id": "w1", "title": "search", "kind": "browser", "parent_source": "schedule", "last_url": "https://shop.example/goods"}
    text = w._handoff_text(r, rep, {})
    assert "Say in the result that shop.example stopped the job and why; nothing else." in text
    assert "take over" not in text
    assert "live view" not in text


# ------------------------------------------------------------------ C3 choice card product id tests
async def test_a_product_card_shows_the_sellers_price_and_picture_from_the_record(tmp_path):
    def chat(req, n):
        last = req.messages[-1]
        if last.get("role") == "tool":
            if last.get("tool_call_id") == "c-quick_replies":
                return say("Here are two options:")
            return say("delegated")
        if HANDOFF_MARK in str(last.get("content")):
            # Chat offers the products as a choice card
            import re
            pids = re.findall(r"- (p[0-9a-f]{10}) ·", str(last.get("content")))
            return call("quick_replies", {
                "questions": [{
                    "question": "Which wash do you want?",
                    "options": ["Gentle wash 1", "Gentle wash 2"],
                    "cards": [
                        {"product": pids[0], "recommended": True},
                        {"product": pids[1]},
                    ],
                }]
            }, cid="c-quick_replies")
        return call("delegate", {"task": "find gentle wash", "kind": "browser"}, cid="c-delegate")

    def worker(req, n):
        if n == 0:
            return call("browser_open", {"url": "https://shop.example/p/1"})
        if n == 1:
            return call("browser_open", {"url": "https://shop.example/p/2"})
        return call("report", {
            "status": "done",
            "summary": "found two items",
            "products": [
                {"name": "Gentle wash 1", "url": "https://shop.example/p/1", "price": "12,900원", "availability": "in_stock", "image_url": "https://img.shop.example/1.jpg"},
                {"name": "Gentle wash 2", "url": "https://shop.example/p/2", "price": "15,900원", "sale_price": "13,900원", "availability": "in_stock"},
            ],
        }, cid=f"c-report-{n}")

    app = _shopping_app(tmp_path, Brain(chat=chat, worker=worker, handoff=chat))

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "find gentle wash"})).json()["id"])
        w = await one_worker(c, "done")

        # Follow until chat reply with replies has appeared
        async def replies_ready():
            msgs = (await c.get("/api/messages")).json()
            return [m for m in msgs if m["role"] == "assistant" and m.get("replies")]

        assistant_msgs = await until(replies_ready)
        cards = assistant_msgs[-1]["replies"]["questions"][0]["cards"]
        assert len(cards) == 2
        assert cards[0]["detail"] == "12,900원 · shop.example"
        assert cards[0].get("recommended") is True
        assert "product" not in cards[0]
        assert cards[1]["detail"] == "13,900원 (15,900원) · shop.example"
        assert "product" not in cards[1]


async def test_mixing_a_product_card_with_a_free_priced_card_is_refused(tmp_path):
    store = Store(":memory:")
    p = Product(id="p0123456789", name="Wash", url="https://shop.example/p/1", seller="shop.example", price="12,900원")
    from assistant.goals import GoalBook
    gb = GoalBook(store)
    tools = gb.tools(current_turn=lambda: {"turn_id": "t1"}, product_of=lambda pid: p if pid == "p0123456789" else None)
    qr = next(t for t in tools if t.name == "quick_replies")
    res = await qr.handler({
        "questions": [{
            "question": "Which wash?",
            "options": ["A", "B"],
            "cards": [
                {"product": "p0123456789"},
                {"detail": "10,000원"},
            ],
        }]
    })
    assert res == "Error: not shown. A choice of products to buy offers only products from a job's report, each card with its product id; do not mix in items from elsewhere."


async def test_an_unknown_product_id_is_refused(tmp_path):
    store = Store(":memory:")
    from assistant.goals import GoalBook
    gb = GoalBook(store)
    tools = gb.tools(current_turn=lambda: {"turn_id": "t1"}, product_of=lambda pid: None)
    qr = next(t for t in tools if t.name == "quick_replies")
    res = await qr.handler({
        "questions": [{
            "question": "Which wash?",
            "options": ["A", "B"],
            "cards": [
                {"product": "p0123456789"},
                {"product": "p9876543210"},
            ],
        }]
    })
    assert res == "Error: not shown. p0123456789 is not a product a job saw on a seller's page. Offer only the ids listed in a job's report; never items or prices from search results or articles."


async def test_cards_without_product_work_as_before(tmp_path):
    store = Store(":memory:")
    from assistant.goals import GoalBook
    gb = GoalBook(store)
    tools = gb.tools(current_turn=lambda: {"turn_id": "t1"}, product_of=lambda pid: None)
    qr = next(t for t in tools if t.name == "quick_replies")
    res = await qr.handler({
        "questions": [{
            "question": "Which room?",
            "options": ["Deluxe", "Standard"],
            "cards": [
                {"detail": "Sea view", "recommended": True},
                {"detail": "City view"},
            ],
        }]
    })
    assert "The buttons are attached to the message" in res


async def test_owner_case_named_seller_refuses_plain_refusal_and_next_step_no_outside_list(tmp_path):
    handoff_text_seen = []
    chat_results = []

    def chat(req, n):
        last = req.messages[-1]
        if last.get("role") == "tool":
            chat_results.append(last["content"])
            # Chat tries quick_replies after handoff, then gives up and sends plain text
            if last.get("tool_call_id") == "c-quick_replies":
                return say("쿠팡에서 확인하지 못했어요. 브라우저를 직접 조작해 로그인하거나 확인하실 수 있어요.")
            return say("맡겼어요.")
        if HANDOFF_MARK in str(last.get("content")):
            handoff_text_seen.append(str(last.get("content")))
            # Chat attempts quick_replies with an invented product id
            return call("quick_replies", {
                "questions": [{
                    "question": "어떤 제품으로 할까요?",
                    "options": ["바디워시 A", "바디워시 B"],
                    "cards": [
                        {"product": "p0123456789"},
                        {"product": "p9876543210"},
                    ],
                }]
            }, cid="c-quick_replies")
        return call("delegate", {
            "kind": "browser",
            "task": "쿠팡에서 저자극 바디워시 구매해줘. Find products that can be bought now ... Sellers: shop.example only (the user named it) ...",
        }, cid="c-delegate")

    def worker(req, n):
        if n == 0:
            return call("browser_open", {"url": "https://shop.example/refused"})
        return call("report", {
            "status": "blocked",
            "reason": "site_refused",
            "summary": "Access denied by shop.example",
        }, cid=f"c-report-{n}")

    app = _shopping_app(tmp_path, Brain(chat=chat, worker=worker, handoff=chat))

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "쿠팡에서 저자극 바디워시 구매해줘"})).json()["id"])
        w = await one_worker(c, "blocked")
        # Wait for chat handoff to run
        await until(lambda: len(handoff_text_seen) > 0)
        h = handoff_text_seen[0]
        assert "did not let Kelo's browser in" in h
        assert "take over" in h
        assert "If no other seller could be opened either, give only the refusal and the sign-in path, no prices." in h
        assert "Never show prices, stock or links from a text search, not even as information." in h
        assert "do not offer another site's items" not in h
        assert "or another shop" not in h

        # Wait for the chat to finish replying
        await until(lambda: any("is not a product a job saw on a seller's page" in r for r in chat_results))
        msgs = (await c.get("/api/messages")).json()
        assistant_msgs = [m for m in msgs if m["role"] == "assistant"]
        assert assistant_msgs
        last_msg = assistant_msgs[-1]
        assert "쿠팡에서 확인하지 못했어요" in last_msg["content"]
        assert not last_msg.get("replies")


async def test_owner_case_requested_seller_refuses_job_continues_on_other_sellers_and_shows_card(tmp_path):
    handoff_text_seen = []
    pid_seen = []

    def chat(req, n):
        last = req.messages[-1]
        if last.get("role") == "tool":
            return say("쿠팡은 확인하지 못했지만 다른 판매처에서 확인한 제품이에요.")
        if HANDOFF_MARK in str(last.get("content")):
            content = str(last.get("content"))
            handoff_text_seen.append(content)
            # Find product ids from handoff text
            import re
            pids = re.findall(r"-\s+(p[0-9a-f]{10})\b", content)
            assert len(pids) >= 2, f"Expected at least 2 product ids in handoff:\n{content}"
            pid_seen.extend(pids)
            return call("quick_replies", {
                "questions": [{
                    "question": "어떤 제품으로 할까요?",
                    "options": ["순한 바디워시 1", "순한 바디워시 2"],
                    "cards": [
                        {"product": pids[0], "recommended": True},
                        {"product": pids[1]},
                    ],
                }]
            }, cid="c-quick_replies")
        return call("delegate", {
            "kind": "browser",
            "task": "쿠팡에서 저자극 바디워시 찾아줘. Find products that can be bought now. Sellers: at most 3 sellers...",
        }, cid="c-delegate")

    def worker(req, n):
        if n == 0:
            return call("browser_open", {"url": "https://shop1.example/refused"})
        if n == 1:
            return call("browser_open", {"url": "https://shop2.example/p/1"})
        if n == 2:
            return call("browser_open", {"url": "https://shop2.example/p/2"})
        return call("report", {
            "status": "done",
            "products": [
                {
                    "name": "Gentle wash 1",
                    "url": "https://shop2.example/p/1",
                    "price": "12,900원",
                    "availability": "in_stock",
                },
                {
                    "name": "Gentle wash 2",
                    "url": "https://shop2.example/p/2",
                    "price": "15,900원",
                    "sale_price": "13,900원",
                    "availability": "in_stock",
                },
            ],
            "summary": "shop1.example refused access. Found 2 products on shop2.example.",
        }, cid=f"c-report-{n}")

    app = _shopping_app(tmp_path, Brain(chat=chat, worker=worker, handoff=chat))

    async with Running(app) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await follow(c, (await c.post("/api/turns", json={"text": "쿠팡에서 저자극 바디워시 찾아줘"})).json()["id"])
        w = await one_worker(c, "done")
        await until(lambda: len(handoff_text_seen) > 0)
        h = handoff_text_seen[0]
        assert "Products this job saw on the sellers' own pages" in h
        assert len(pid_seen) >= 2
        # Wait for the chat to reply with replies
        async def replies_ready():
            msgs = (await c.get("/api/messages")).json()
            return [m for m in msgs if m["role"] == "assistant" and m.get("replies")]

        assistant_msgs = await until(replies_ready)
        last_msg = assistant_msgs[-1]
        assert "쿠팡은 확인하지 못했지만" in last_msg["content"]
        cards = last_msg["replies"]["questions"][0]["cards"]
        assert len(cards) == 2
        assert "12,900원 · shop2.example" in cards[0]["detail"]
        assert "product" not in cards[0]
        assert "13,900원 (15,900원) · shop2.example" in cards[1]["detail"]


# ------------------------------------------------------------------ C5 texts tests
def test_every_buying_text_carries_the_shared_shopping_rule():
    from pathlib import Path
    from assistant.prompt import PromptBuilder
    content = Path(__file__).resolve().parents[1] / "content"
    buying = lambda t: t.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0]
    for pay in (False, True):
        for browser in (False, True):
            builder = PromptBuilder.load(content, None, None, pay_split=pay, browser_split=browser)
            text = buying(builder.base)
            assert "Items you offer to buy come only from a browser job's product report" in text
            assert "Prices, stock or links from a text search are never shown to the user, not even as information." in text
            assert "When the requested seller refuses Kelo's browser or needs a sign-in, the same browser job goes on to up to two other established sellers" in text
            assert "The answer says in one line that the requested seller could not be checked" in text
            assert "If no other seller could be opened either: only the refusal and the sign-in path, no prices." in text
            assert "never a list in your text" in text
            assert "one short takeaway line" in text
            assert "name at most two products" in text
            assert "never a card for budget or soft preferences" in text
            assert "no separate confirmation when the item and variant are already clear" in text
            assert "at most 3 sellers and 10 products" in text
            # Removed phrases
            assert "you may share what was found elsewhere as information" not in text
            assert "another seller only if the user allows it" not in text
            assert "with prices; no exact item named" not in text
            assert "never as verified buyable candidates" not in text
            assert "no choice card from unverified prices" not in text


def test_brief_template_in_skill_carries_reference_constraints():
    from pathlib import Path
    content = Path(__file__).resolve().parents[1] / "content"
    skill_text = (content / "skills" / "shopping-and-bookings" / "SKILL.md").read_text(encoding="utf-8")
    assert "at most 3 sellers" in skill_text
    assert "at most 10 products" in skill_text
    assert "each product from the product's own page" in skill_text or "open each product's own page" in skill_text
    assert "in_stock" in skill_text and "cart" in skill_text
    assert "Discovery only: no cart, no checkout, no sign-up" in skill_text
    assert "you may share what was found elsewhere as information" not in skill_text
    assert "another seller only if the user allows it" not in skill_text



def test_shopping_texts_name_no_store_or_site():
    from pathlib import Path
    import re
    from assistant.goals import GoalBook
    from assistant.tools.web import WebTools

    content = Path(__file__).resolve().parents[1] / "content"
    texts = []
    prompts_dir = content / "prompts"
    for name in ("buying-job-specialist.md", "buying-job.md", "buying-specialist.md"):
        texts.append((prompts_dir / name).read_text(encoding="utf-8"))
    sys_text = (prompts_dir / "system.md").read_text(encoding="utf-8")
    texts.append(sys_text.split("**Buying, ordering, booking.**", 1)[1].split("\n\n**", 1)[0])

    skill_text = (content / "skills" / "shopping-and-bookings" / "SKILL.md").read_text(encoding="utf-8")
    texts.append(skill_text)

    wt = WebTools()
    ws_desc = next(t for t in wt.tools() if t.name == "web_search").description
    texts.append(ws_desc)

    gb = GoalBook(Store(":memory:"))
    qr_desc = next(t for t in gb.tools() if t.name == "quick_replies").description
    texts.append(qr_desc)

    w = _make_workers(Store(":memory:"))
    handoff_text = w._handoff_text(
        {"id": "w1", "title": "t", "kind": "browser", "parent_source": "chat", "last_url": ""},
        {"status": "blocked", "reason": "site_refused", "summary": "s", "details": ""},
        {}
    )
    texts.append(handoff_text)

    domain_re = re.compile(r"\b[a-z0-9-]+\.(com|net|kr|co|jp|org)\b")
    for t in texts:
        m = domain_re.search(t)
        assert m is None, f"Found domain {m.group(0)!r} in text: {t[:100]}..."


def test_web_search_says_results_are_background_not_offers():
    from assistant.tools.web import WebTools
    wt = WebTools()
    ws_desc = next(t for t in wt.tools() if t.name == "web_search").description
    assert "Results and articles are background: never offer a price, stock or link from them as something the user can buy now." in ws_desc








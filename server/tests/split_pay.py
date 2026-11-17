"""The payment split for tests (docs/browser-workers.md, tool_roles.py). With the split on (the default) the chat cannot pay: a final
checkout step goes to a job of kind "checkout", and only that job's model has the paying tools. These helpers let a test that pays
run both ways: the old chat path with the switch off, and the same payment made by a checkout job with it on.

Split(jobs, closing) is a role-aware fake model: the chat hands each turn's payment to one checkout job (delegate, kind checkout,
with a url), each job makes the calls it is given in order and then reports done, and the chat's closing text answers the hand-over.
settle(c) waits until no job runs (one checkout job per shop: a second one while the first is open is refused)."""

import asyncio
import json

from assistant.providers import Done, TextDelta, ToolCall

from test_workers import role_of

SHOP = "https://shop.example.com/checkout"


def job(*calls):
    """One turn of the chat that hands a checkout job over; the job makes these (tool name, args) calls, then reports done."""
    return ("job", list(calls))


def direct(name):
    """One turn that the chat answers itself with a tool of its own (a tool the split does not take from it)."""
    return ("chat", name)


class Split:
    def __init__(self, *turns, closing="ok", url=SHOP):
        self.turns, self.closing, self.url = list(turns), closing, url
        self.turn = -1               # the chat turn being answered
        self.queue: list[list] = []  # the steps of the jobs handed over, in order
        self.job_no = -1
        self.chat_seen, self.worker_seen = [], []

    async def __call__(self, provider, request, **kw):
        role = role_of(request)
        if role == "worker":
            self.worker_seen.append(request)
            for ev in self._worker(request):
                yield ev
        elif role == "handoff":
            yield TextDelta(self.closing); yield Done("stop")
        elif not any(m.get("role") == "system" and "You are Kelo" in str(m.get("content")) for m in request.messages):
            yield TextDelta("결제 단계"); yield Done("stop")   # the task's own name (a request with no chat prompt): not a turn
        else:
            self.chat_seen.append(request)
            if request.messages[-1].get("role") == "tool":
                for ev in (TextDelta(self.closing), Done("stop")):
                    yield ev
                return
            self.turn += 1
            kind, what = self.turns[self.turn]
            if kind == "chat":
                yield ToolCall(f"t{self.turn}", what, "{}")
                yield Done("tool_calls")
            else:
                self.queue.append(what)
                args = {"task": f"pay step {self.turn}", "kind": "checkout", "url": self.url}
                yield ToolCall(f"d{self.turn}", "delegate", json.dumps(args, ensure_ascii=False))
                yield Done("tool_calls")

    def _worker(self, request):
        results = [m for m in request.messages if m.get("role") == "tool"]
        if not results:
            self.job_no += 1                                  # a job's first request: its first step
            self.steps = self.queue.pop(0)
        steps = self.steps
        if len(results) < len(steps):
            name, args = steps[len(results)]
            yield ToolCall(f"j{self.job_no}-{len(results)}", name, json.dumps(args, ensure_ascii=False))
            yield Done("tool_calls")
        else:
            yield ToolCall(f"j{self.job_no}-report", "report", json.dumps({"status": "done", "summary": "결제 단계를 마쳤어요."}, ensure_ascii=False))
            yield Done("tool_calls")


async def settle(c, tries=400):
    """Wait until no job is running (a finished job frees its shop for the next checkout job)."""
    for _ in range(tries):
        items = (await c.get("/api/workers")).json()["items"]
        if not any(i["status"] == "running" for i in items):
            return items
        await asyncio.sleep(0.02)
    raise AssertionError("a job is still running")


def app_args(split):
    """(Settings kwargs, create_app kwargs) for an app whose payment split is `split` (None = the default, on). With it on a copy gets a
    browser, so a checkout job can run."""
    if split is None:
        return {}, {}
    if not split:
        return {"payment_specialist": False}, {}
    from assistant.tools.browser import BrowserSession
    from test_agent_profile import ShotPage

    async def connect():
        return ShotPage(SHOP)
    return {"payment_specialist": True}, {"browser": BrowserSession(connect=connect), "with_worker_tools": True}

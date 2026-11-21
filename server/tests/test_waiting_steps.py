"""A task that waits for the AI model keeps its tool steps (owner 2026-11-20, S2.4 test 5): the stop report reads them if the wait is
given up (stop_reports.py). Fake model: one tool step, then the model is down. No real model."""

import json

import httpx

from assistant.providers import Done, ProviderError, ToolCall
from assistant.store import Store

from test_resume import AUTH, CHECK, Running, _status, app_for, until


class ReadThenDown:
    """The first answer calls one tool (goal_get); every later call finds the model down."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, provider, request, **kw):
        if request.messages and request.messages[-1].get("content") == CHECK:
            raise ProviderError("p: service unavailable", status=503)
        self.calls += 1
        if self.calls == 1:
            yield ToolCall(id="c1", name="goal_get", arguments=json.dumps({"goal_id": 1}))
            yield Done("tool_calls")
            return
        raise ProviderError("p: overloaded, try again later", status=529)


async def test_a_task_waiting_for_the_model_keeps_its_steps(tmp_path):
    m = ReadThenDown()
    async with Running(app_for(tmp_path, m)) as srv, httpx.AsyncClient(base_url=srv.url, headers=AUTH) as c:
        await c.post("/api/goals", json={"title": "Goal A"})
        t = await until(lambda: _status(c, "waiting_model"))
    steps = json.loads(Store(str(tmp_path / "a.db"))._all("SELECT steps FROM tasks WHERE id = ?", (t["id"],))[0]["steps"] or "{}")
    assert [s["tool"] for s in steps.get("steps", [])] == ["goal_get"]

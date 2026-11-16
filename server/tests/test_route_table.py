"""The API stays what the apps know: every route, in order, with its methods, status code, auth and parameters (route_table.py).

A failure after a refactor means a route moved, vanished or changed shape. If the change is on purpose, regenerate the fixture
with `uv run python server/tests/route_table.py` and say so in the commit."""

import route_table


def test_route_table_unchanged():
    want = route_table.FIXTURE.read_text()
    got = route_table.dumps(route_table.build())
    assert got == want

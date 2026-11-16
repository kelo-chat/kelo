"""The server's route table, in registration order: path, methods, endpoint name, status code, auth dependencies and every
parameter (path, query, header, body model). server/tests/test_route_table.py compares it with server/tests/fixtures/route_table.json so a
refactor of server.py (routers, services) cannot change the API by accident.

    uv run python server/tests/route_table.py            # rewrite the fixture (only when the API changes on purpose)
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "route_table.json"


def _param(p) -> dict:
    field = p.field_info
    ann = field.annotation
    required = field.is_required()
    # a model's module is left out ("assistant.server.PairIn | None" -> "PairIn | None"): moving a model to a router keeps the API
    type_name = re.sub(r"\bassistant(\.\w+)*\.", "", getattr(ann, "__name__", str(ann)))
    out = {"name": p.name, "alias": p.alias, "required": required, "type": type_name,
           "kind": type(field).__name__}
    default = None if required else field.default
    try:
        json.dumps(default)
        out["default"] = default
    except TypeError:
        out["default"] = repr(default)
    return out


def _name(obj) -> str:
    return getattr(obj, "__name__", type(obj).__name__)


def _deps(dependant) -> list[str]:
    names = []
    for d in dependant.dependencies:
        call = d.call
        names.append(getattr(call, "__name__", type(call).__name__))
        names.extend(f"{names[-1]}>{n}" for n in _deps(d))
    return names


def _route_row(kind: str, path: str, r, d) -> dict:
    """`r` has the route's own fields (an APIRoute, or the effective context of a route inside an included router)."""
    return {
        "kind": kind, "path": path,
        "methods": sorted(r.methods or []), "name": r.name, "status": r.status_code, "schema": r.include_in_schema,
        "response_class": _name(getattr(r.response_class, "value", r.response_class)),
        "deps": _deps(d),
        "path_params": [_param(p) for p in d.path_params],
        "query": [_param(p) for p in d.query_params],
        "headers": [_param(p) for p in d.header_params],
        "body": [_param(p) for p in d.body_params],
    }


def _rows(routes) -> list[dict]:
    from fastapi.routing import APIRoute, APIWebSocketRoute, _EffectiveRouteContext, _IncludedRouter
    rows = []
    for r in routes:
        if isinstance(r, _IncludedRouter):   # include_router keeps one branch in place: its routes match there, in order
            rows.extend(_rows(r.effective_candidates()))
        elif isinstance(r, _EffectiveRouteContext):
            o = r.original_route
            if isinstance(o, APIRoute):
                rows.append(_route_row("APIRoute", r.path, r, r.dependant))
            elif isinstance(o, APIWebSocketRoute):
                s = r.starlette_route
                rows.append({"kind": "APIWebSocketRoute", "path": s.path, "name": s.name, "deps": _deps(s.dependant),
                             "query": [_param(p) for p in s.dependant.query_params]})
            else:
                rows.append({"kind": type(o).__name__, "path": r.path, "name": r.name})
        elif isinstance(r, APIRoute):
            rows.append(_route_row("APIRoute", r.path, r, r.dependant))
        elif isinstance(r, APIWebSocketRoute):
            rows.append({"kind": "APIWebSocketRoute", "path": r.path, "name": r.name, "deps": _deps(r.dependant),
                         "query": [_param(p) for p in r.dependant.query_params]})
        else:
            rows.append({"kind": type(r).__name__, "path": getattr(r, "path", ""), "name": getattr(r, "name", "")})
    return rows


def table(app) -> list[dict]:
    return _rows(app.router.routes)


def build() -> dict:
    sys.path.insert(0, str(ROOT / "server" / "src"))
    from assistant.server import Settings, create_app
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        state = Path(tmp) / "state"
        home = Path(tmp) / "home"
        home.mkdir()
        configs = {
            "minimal": dict(settings=Settings(token="t", cdp_host=None), kw=dict(tools=[])),
            "full": dict(settings=Settings(token="t", provider="p", model="m", cdp_host=None, db_path=str(state / "assistant.db"),
                                           home_dir=str(home), content_dir=str(ROOT / "server" / "content"), scheduler=False), kw=dict(tools=None)),
        }
        for name, c in configs.items():
            out[name] = table(create_app(c["settings"], **c["kw"]))
    return out


def dumps(data: dict) -> str:
    return json.dumps(data, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    FIXTURE.write_text(dumps(build()))
    print(f"wrote {FIXTURE.relative_to(ROOT)}")

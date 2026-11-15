"""The application object the routers share (see api/__init__.py for the map).

`create_app()` in server.py builds the services once (store, settings, push, wallet, ...) and puts them on one `AppContext`,
then includes each domain's router, built from that context. A router module takes what it uses from the context at the top of
its `router(ctx)` function, so its dependencies are listed in one place; it never reaches into another router.

Names a router hands back (helpers other parts of create_app still call) are put on the context with `ctx.add(...)` too.
"""

from __future__ import annotations

from typing import Any


class AppContext:
    """Services and shared helpers of one running app. Attributes are added by create_app (and by routers that export helpers)."""

    def __init__(self, **kw: Any):
        self.add(**kw)

    def add(self, **kw: Any) -> None:
        for name, value in kw.items():
            setattr(self, name, value)

    def __getattr__(self, name: str) -> Any:   # only called for names never added: say which one
        raise AttributeError(f"AppContext has no {name!r}: add it in create_app before the router that needs it is built")

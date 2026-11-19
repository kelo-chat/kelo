"""One browser window per task (brief C1a): the tabs of one task key, which of them is active, and which one opened which. Pure model:
no Playwright, no I/O. BrowserSession (browser.py) keeps one TaskWindow per tab key and the pages in it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Tab:
    id: str                       # "t1", "t2", ... per window, never reused
    page: Any
    opener: str = ""              # id of the tab that opened it ("" = the first tab, or opened by browser_tabs or the owner)
    view_cdp: Any = None          # the per-page DevTools sessions BrowserSession keeps for the active tab
    tab_cdp: Any = None
    target: tuple[str, str] | None = None


@dataclass(slots=True)
class TaskWindow:
    key: str
    profile: str = ""             # "" = the default profile; a named profile's id otherwise; fixed for the window's life
    tabs: list[Tab] = field(default_factory=list)
    active: str = ""
    next_id: int = 1

    def add(self, page: Any, opener: str = "") -> Tab:
        """A new tab, appended in opening order and NOT activated (the caller decides)."""
        tab = Tab(id=f"t{self.next_id}", page=page, opener=opener)
        self.next_id += 1
        self.tabs.append(tab)
        return tab

    def find(self, page: Any) -> Tab | None:
        return next((t for t in self.tabs if t.page is page), None)

    def get(self, tab_id: str) -> Tab | None:
        return next((t for t in self.tabs if t.id == tab_id), None)

    def active_tab(self) -> Tab | None:
        return self.get(self.active) if self.active else None

    def activate(self, tab_id: str) -> Tab:
        tab = self.get(tab_id)
        if tab is None:
            raise KeyError(tab_id)
        self.active = tab.id
        return tab

    def remove(self, page: Any) -> Tab | None:
        """Drops the tab of `page`. When it was the active one, the active tab becomes its opener if that is still open, else the last
        tab, else nothing. Returns the removed tab (None when the page is not in the window)."""
        tab = self.find(page)
        if tab is None:
            return None
        index = self.tabs.index(tab)
        self.tabs.pop(index)
        if self.active == tab.id:
            opener = self.get(tab.opener) if tab.opener else None
            if opener is not None:
                self.active = opener.id
            else:
                self.active = self.tabs[-1].id if self.tabs else ""
        return tab

    def listing(self) -> list[dict[str, Any]]:
        """The tabs in opening order: id, address (filled by the caller from the page), the active flag and the opener's id."""
        return [{"id": t.id, "url": str(getattr(t.page, "url", "") or ""), "active": t.id == self.active, "opener": t.opener}
                for t in self.tabs]

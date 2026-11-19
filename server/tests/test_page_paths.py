"""Kelo's page scripts leave no mark in a page (addendum 9 of the browser-identity card): elements are found again by a CSS path that is
computed on the page (page_paths.py). The static test scans the tools' source; the Chromium test checks that a path names the same element
the script picked, also when ids repeat, and that the page gets no attribute from it."""

import re
from pathlib import Path

import pytest

from assistant.tools import page_paths

TOOLS = Path(page_paths.__file__).parent
MARK = re.compile(r"""setAttribute\(\s*['"]data-(kelo|vault)-|['"]data-(kelo|vault)-[a-z]+['"]\s*,\s*['"]""")


def test_no_tool_writes_a_kelo_or_vault_attribute_into_a_page():
    hits = []
    for path in sorted(TOOLS.glob("*.py")):
        if path.name == "page_paths.py":
            continue   # its docstring names the old marks
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if MARK.search(line) or "removeAttribute('data-kelo" in line or "removeAttribute('data-vault" in line:
                hits.append(f"{path.name}:{n}")
    assert hits == []


def test_with_path_puts_the_function_into_the_script_body():
    js = page_paths.with_path("(x) => { return keloPath(x); }")
    assert "function keloPath(e)" in js and js.index("function keloPath") < js.index("return keloPath(x)")
    assert js.startswith("(x) => {") and js.endswith("return keloPath(x); }")


pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no Playwright Chromium here ({type(e).__name__})")
        c = await b.new_context()
        pg = await c.new_page()
        await pg.set_content(
            "<!doctype html><body><div id='dup'><input name=a></div><div id='dup'><input name=b></div>"
            "<section><p>x</p><input id=\"q.1\" name=c><input name=d></section></body>")
        yield pg
        await b.close()


async def test_a_path_names_the_element_the_script_picked_and_marks_nothing(page):
    js = page_paths.with_path("() => { return [...document.querySelectorAll('input')].map((e) => keloPath(e)); }")
    paths = await page.evaluate(js)
    assert len(paths) == 4 and len(set(paths)) == 4                       # two inputs under the same id are told apart
    for path, name in zip(paths, ["a", "b", "c", "d"]):
        assert await page.locator(path).get_attribute("name") == name     # the path finds that same element
    assert await page.evaluate("() => document.querySelectorAll('[data-kelo-cc],[data-kelo-otp],[data-vault-u],[data-vault-p]').length") == 0
    assert await page.evaluate("() => document.querySelectorAll('[data-kelo-opt]').length") == 0


async def test_a_page_script_that_uses_paths_runs_in_the_page(page):
    js = page_paths.with_path("(n) => { const e = document.querySelector('section input'); return keloPath(e) + ' #' + n; }")
    path = await page.evaluate(js, 7)
    assert path.endswith(" #7") and await page.locator(path.rsplit(" #", 1)[0]).get_attribute("name") == "c"

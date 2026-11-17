"""hc-final-a (docs/no-word-lists.md): chat_images.SECURE_RECTS_JS covers the secure fields of a page by structure only: the password
type, the autocomplete tokens, a masked rendering (-webkit-text-security), and the visible fields of a card form. A field's name or id
never decides. Real Chromium on local fixture pages (skipped without Playwright's browser); no real site."""

from __future__ import annotations

import pytest

from assistant.chat_images import SECURE_RECTS_JS

pw = pytest.importorskip("playwright.async_api")


@pytest.fixture
async def page():
    async with pw.async_playwright() as p:
        try:
            b = await p.chromium.launch(headless=True)
        except Exception as e:   # no browser installed
            pytest.skip(f"no chromium: {e}")
        pg = await b.new_page()
        yield pg
        await b.close()


async def covered(page, body: str) -> int:
    await page.set_content(f"<!doctype html><body>{body}</body>")
    return len((await page.evaluate(SECURE_RECTS_JS))["rects"])


async def test_a_card_form_covers_every_visible_field_of_it_whatever_they_are_called(page):
    body = "<form><input autocomplete=cc-number><input maxlength=3><select><option>1</option></select><input type=hidden value=x></form>"
    assert await covered(page, body) == 3                  # the card number, the unnamed 3-digit box, the select; the hidden field is not a box


async def test_a_card_token_without_a_form_covers_the_nearest_box_holding_two_fields(page):
    body = "<div><input autocomplete='cc-exp'><input placeholder=anything></div><div><input name=other></div>"
    assert await covered(page, body) == 2


async def test_a_field_named_cvv_or_card_outside_a_card_form_is_NOT_covered(page):
    assert await covered(page, "<input name=cvv><input id=card_number><input name=pin>") == 0
    assert await covered(page, "<input name=cvc><input id=cvv>") == 0


async def test_a_masked_text_field_is_covered_whatever_it_is_called(page):
    assert await covered(page, "<input style='-webkit-text-security: disc' name=anything>") == 1
    assert await covered(page, "<input style='-webkit-text-security: none' name=anything>") == 0


async def test_the_password_type_and_the_login_tokens_stay_covered(page):
    assert await covered(page, "<input type=password>") == 1
    assert await covered(page, "<input autocomplete='one-time-code'><input autocomplete='new-password'>") == 2
    assert await covered(page, "<input autocomplete=username>") == 0


async def test_hidden_or_zero_size_fields_are_not_boxes(page):
    assert await covered(page, "<input type=password style='display:none'>") == 0

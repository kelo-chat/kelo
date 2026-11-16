"""The screen reader (android/screen.py): a uiautomator dump -> items with refs in reading order, the model's text, the judge's words."""

import pathlib

import pytest

from assistant.android.judge import tap_kind, type_kind
from assistant.android.screen import MAX_ITEMS, Item, ScreenReader

FIX = pathlib.Path(__file__).parent / "fixtures" / "android"
READ = ScreenReader()


def fixture(name, app="", start=1):
    return READ.parse((FIX / f"{name}.xml").read_text(encoding="utf-8"), app, start)


def node(text="", desc="", cls="android.widget.TextView", bounds="[0,0][100,50]", clickable="false", extra="", children=""):
    return (f'<node text="{text}" content-desc="{desc}" class="{cls}" package="p" clickable="{clickable}" checkable="false" checked="false" '
            f'password="false" bounds="{bounds}" {extra}>{children}</node>')


def dump(*children, size="[0,0][720,1280]"):
    return f'<?xml version=\'1.0\' encoding=\'UTF-8\' standalone=\'yes\' ?><hierarchy rotation="0">{node(bounds=size, children="".join(children))}</hierarchy>'


def test_a_real_settings_dump_reads_as_rows_in_reading_order():
    s = fixture("settings", "com.android.settings/.Settings$AppDashboardActivity")
    assert [(i.ref, i.kind, i.words) for i in s.items] == [
        ("a1", "text", "Apps"), ("a2", "button", "Navigate up"), ("a3", "button", "All apps 10 apps installed"),
        ("a4", "button", "Default apps WebView Shell"), ("a5", "button", "Unused apps 0 unused apps"), ("a6", "button", "Special app access")]
    assert s.size == (720, 1280) and s.package == "com.android.settings"
    text = s.render()
    assert text.splitlines()[0] == "App: com.android.settings (Settings$AppDashboardActivity)   720x1280"
    assert 'a3 button "All apps 10 apps installed"' in text


def test_a_clickable_parent_takes_the_words_of_what_is_inside_it_and_those_are_not_items_of_their_own():
    s = fixture("pay")
    assert [(i.kind, i.words) for i in s.items] == [("text", "주문 요약"), ("text", "12,900원"), ("check", "선물 포장"), ("button", "결제하기")]
    assert s.items[2].checked is True and 'a3 check "선물 포장" (on)' in s.render()


def test_a_field_shows_its_hint_and_whether_it_is_empty_or_what_is_typed():
    s = fixture("review_form", "com.example.shop/com.example.shop.ReviewWriteActivity")
    edit = s.item("a3")
    assert edit.kind == "edit" and edit.editable and edit.hint.startswith("상품에")
    assert 'a3 edit "상품에 대한 솔직한 평가를 남겨주세요" (empty)' in s.render()
    typed = READ.parse(dump(node(text="맛있어요", cls="android.widget.EditText", bounds="[0,100][700,200]", extra='hint="리뷰"')))
    assert typed.render().splitlines()[1] == 'a1 edit "리뷰" = "맛있어요"'
    older = READ.parse(dump(node(text="리뷰", cls="android.widget.EditText", bounds="[0,100][700,200]", extra='hint="리뷰"')))
    assert older.render().splitlines()[1] == 'a1 edit "리뷰" (empty)'           # an old Android shows the hint as the text
    secret = READ.parse(dump(node(text="••••", cls="android.widget.EditText", bounds="[0,100][700,200]", extra='hint="Password" password="true"').replace('password="false"', "")))
    assert "••••" not in secret.render() and "(password, filled)" in secret.render()


def test_nodes_with_no_area_or_off_the_screen_are_left_out():
    s = fixture("review_form")
    words = [i.words for i in s.items]
    assert "hidden" not in words and "far away" not in words and "등록하기" in words


def test_refs_follow_reading_order_top_then_left():
    s = READ.parse(dump(node("low", bounds="[0,600][100,650]"), node("right", bounds="[300,100][400,150]"), node("left", bounds="[0,100][100,150]")))
    assert [(i.ref, i.text) for i in s.items] == [("a1", "left"), ("a2", "right"), ("a3", "low")]


def test_a_second_screen_goes_on_counting_so_an_old_ref_never_names_something_new():
    first = fixture("pay")
    second = fixture("review_form", start=1 + len(first.items))
    assert first.item("a4").words == "결제하기"
    assert second.item("a4") is None and second.item("a5") is not None
    assert not {i.ref for i in first.items} & {i.ref for i in second.items}


def test_at_most_120_items_and_the_rest_is_counted():
    many = dump(*(node(f"row {n}", bounds=f"[0,{n * 9}][100,{n * 9 + 8}]") for n in range(130)))
    s = READ.parse(many)
    assert len(s.items) == MAX_ITEMS == 120 and s.more == 10
    assert s.render().splitlines()[-1] == "… 10 more, scroll" and s.item("a120").text == "row 119" and s.item("a121") is None


def test_a_long_merged_label_is_cut_to_80_characters():
    s = READ.parse(dump(node(clickable="true", bounds="[0,0][700,300]", children=node("word " * 40, bounds="[0,0][700,100]"))))
    assert len(s.items[0].text) <= 80 and s.items[0].text.endswith("…")


def test_a_dump_that_is_not_xml_or_declares_entities_is_refused():
    with pytest.raises(ValueError):
        READ.parse("<hierarchy")
    with pytest.raises(ValueError):
        READ.parse('<!DOCTYPE x [<!ENTITY a "b">]><hierarchy rotation="0"/>')


def test_at_finds_the_smallest_item_under_a_point():
    s = fixture("review_form")
    assert s.at(100, 500).kind == "edit" and s.at(300, 1050).words == "등록하기" and s.at(700, 1250) is None


# ---- judge
def item(text="", desc="", hint="", **kw):
    return Item("a1", "button", text=text, desc=desc, hint=hint, **kw)


@pytest.mark.parametrize("words,kind", [("결제하기", "pay"), ("등록하기", "send"), ("설정", ""), ("Pay now", "pay"), ("Navigate up", "")])
def test_tap_kind_follows_the_browsers_words(words, kind):
    assert tap_kind(item(words)) == kind


def test_tap_kind_reads_the_description_when_there_is_no_text():
    assert tap_kind(item(desc="결제하기")) == "pay"


def test_type_kind_asks_for_personal_details_and_not_for_a_review():
    review = Item("a3", "edit", hint="상품에 대한 솔직한 평가를 남겨주세요", multiline=True, editable=True)
    assert type_kind(review, "소스가 맛있어요") == ""
    assert type_kind(review, "연락처 010-1234-5678") == "personal"
    assert type_kind(Item("a4", "edit", hint="Email", editable=True), "x") == "personal"
    assert type_kind(Item("a5", "edit", hint="Password", password=True, editable=True), "hunter2") == "personal"
    assert type_kind(None, "kim@example.org") == "personal" and type_kind(None, "hello") == ""

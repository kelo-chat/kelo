"""App rule (owner 2026-11-17: "서버주소랑 주소 바꾸기는 똑같은 창이 열리는데?"): My shows the server address in ONE row, and that row is
the one that opens the change-address sheet. A second row opening the same sheet is the bug."""
import re
from pathlib import Path

APPS = Path(__file__).resolve().parents[2] / "apps"
KOTLIN = APPS / "shared/src/commonMain/kotlin/chat/kelo/app/my/MyScreen.kt"
SWIFT = APPS / "ios/KeloUI/Sources/KeloMy/MyScreen.swift"


def openers_kotlin(text: str) -> int:
    return len(re.findall(r"MenuRow\([^\n]*onChangeAddress\(\)", text))


def openers_swift(text: str) -> int:
    return len(re.findall(r"MenuRow\([^\n]*changeAddress = true", text))


def test_one_row_opens_the_change_address_sheet():
    assert openers_kotlin(KOTLIN.read_text(encoding="utf-8")) == 1
    assert openers_swift(SWIFT.read_text(encoding="utf-8")) == 1


def test_the_rule_sees_two_rows():
    two_kt = ("row { MenuRow(KeloIcons.Cloud, string(Res.string.server_address_row), { onChangeAddress() }, value = base) }\n"
              "row { MenuRow(KeloIcons.Edit, string(Res.string.server_address_change), { onChangeAddress() }) }")
    two_sw = ("MenuRow(.cloud, L(R.string.server_address_row), value: base) { changeAddress = true }\n"
              "MenuRow(.edit, L(R.string.server_address_change)) { changeAddress = true }")
    assert openers_kotlin(two_kt) == 2 and openers_swift(two_sw) == 2

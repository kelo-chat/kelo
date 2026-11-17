"""A stand-in for the owner's main model behind page_judge.PageJudge, for tests only (no model, no network). It "reads" the payload the
way the model would, with a few words of its own: the words live in the TEST, never in the server code. `ask(system, user)` is what
model_json.ModelJson.ask is. `calls` counts the requests per kind; `down=True` makes it unavailable (None)."""

from __future__ import annotations

import json
from typing import Any

from assistant import page_judge as pj

APP = ("앱에서", "승인", "approve", "scan", "push")
PASSKEY = ("passkey", "패스키", "지문", "fingerprint")
OTP = ("one-time", "인증번호를 입력", "verification code", "enter the code")
EXPIRED = ("만료", "expired")
RESEND = ("재요청", "send again", "resend", "new code")
ERASE = ("지우기", "clear", "delete", "←")
FIELD_WORDS = {
    "card": [("number", ("card number", "카드번호", "cardnumber", "cc num")), ("cvc", ("cvc", "cvv", "보안코드", "security code")),
             ("exp_month", ("유효기간 월", "exp month", "expiry month", "month")), ("exp_year", ("유효기간 년", "exp year", "expiry year", "year")),
             ("exp", ("exp-date", "expiry", "mm/yy", "mm / yy", "유효기간")), ("name", ("name on card",)), ("postal", ())],
    "shipping": [("note", ("메모", "memo", "hinweis", "instruction", "備考")), ("tel_country", ("vorwahl", "country code")),
                 ("tel", ("phone", "telefon", "電話", "전화", "휴대폰")), ("postal", ("zip", "postal", "postcode", "plz", "우편", "郵便")),
                 ("line2", ("address 2", "line 2", "상세", "建物", "zusatz", "address line 2")), ("level1", ("state", "county", "都道府県", "시/도")),
                 ("level2", ("city", "town", "ort", "市区町村", "시/군")), ("country", ("country", "land", "국가")),
                 ("family", ("last name", "surname", "nachname", "姓", "성")), ("given", ("first name", "vorname", "名", "이름")),
                 ("name", ("full name", "받는", "name")), ("line1", ("address line 1", "street", "straße", "주소", "番地", "address"))],
}


class FakePageJudge:
    def __init__(self, down: bool = False) -> None:
        self.down = down
        self.calls: list[str] = []

    async def ask(self, system: str, user: str) -> dict[str, Any] | None:
        if self.down:
            return None
        data = json.loads(user)
        low = str(data.get("text") or "").lower()
        if system == pj.STEP_SYSTEM:
            self.calls.append("step")
            kind = "passkey" if any(w in low for w in PASSKEY) else "otp" if any(w in low for w in OTP) else "app" if any(w in low for w in APP) else "none"
            controls = [c.lower() for c in data.get("controls") or []]
            resend = next((i for i, c in enumerate(controls) if any(w in c for w in RESEND)), None)
            lines = [ln.lower() for ln in data.get("lines") or []]
            line = next((i for i, ln in enumerate(lines) if any(w in ln for w in APP + PASSKEY + OTP)), None)
            return {"kind": kind, "expired": any(w in low for w in EXPIRED) and "display:none" not in low and "다시 요청" in low, "seconds": None,
                    "code": 0 if data.get("codes") and kind == "app" else None, "qr": "qr" in low, "resend": resend, "line": line}
        if system == pj.VERIFY_SYSTEM:
            self.calls.append("verify")
            step = "approve" if any(w in low for w in ("approve", "승인", "phone", "휴대폰", "check your")) else "code" if any(
                w in low for w in ("code", "코드", "번호")) else "none"
            return {"step": step}
        if system == pj.ERASE_SYSTEM:
            self.calls.append("erase")
            return {"erase": next((c["index"] for c in data["controls"] if any(w in c["label"].lower() for w in ERASE)), None)}
        if system == pj.FIELDS_SYSTEM:
            self.calls.append("fields")
            purpose = "card" if "number" in data["kinds"] else "shipping"
            out: dict[str, str] = {}
            used: dict[str, int] = {}
            for f in data["fields"]:
                text = " ".join(str(f.get(k) or "") for k in ("label", "placeholder")).lower()
                if f.get("type") == "email" or any(w in text for w in ("coupon", "쿠폰", "mail", "メール", "birth", "month" if purpose == "shipping" else "@@")):
                    continue
                for kind, words in FIELD_WORDS[purpose]:
                    if any(w in text for w in words):
                        used[kind] = used.get(kind, 0) + 1
                        out[f["ref"]] = kind
                        break
            return {"fields": out}
        return None

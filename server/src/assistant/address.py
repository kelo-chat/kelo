"""Addresses for any country, from open metadata (no country has code of its own here).

`data/address_data.json` is Google's address metadata (the data behind libaddressinput and Chromium's address forms; CC-BY 4.0, see
NOTICE and scripts/fetch_address_data.py): for each country the order of the fields (`fmt`), which are required, the postal-code
pattern, what the fields are called there (state / province / prefecture / do-si ..., ZIP / postal code / PIN ...) and the list of its
first-level regions. Plus the calling code of each country (libphonenumber, Apache 2.0) so phones are kept as E.164, and each time zone's
country (IANA zone.tab) for the default country of a new address.

`data/address_local.json` holds, as data too, where shops in a country ask differently from the postal format (Korean shops ask for a
postal code, the address from a postcode search and the details; Japanese shops for prefecture, municipality, block number and building).

What the apps get (`form`): the fields of one country in its order, with labels in the user's language for that country's names of the
fields, which are required, the regions as a list where the metadata has one, and the postal-code pattern with an example. All three apps
render that list, so the same country gives the same form everywhere. A saved address (`clean`) is structured fields plus the country
code and the phone in E.164; `formatted` writes it in the country's own postal format (the metadata's `fmt`).

Field keys: name, organization, address_line1, address_line2, dependent_locality (D), locality (C), admin_area (S), postal_code (Z),
sorting_code (X).
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from .i18n import N_, t

DATA = Path(__file__).parent / "data"
TOKENS = {"N": "name", "O": "organization", "D": "dependent_locality", "C": "locality", "S": "admin_area", "Z": "postal_code",
          "X": "sorting_code"}
FIELDS = ("name", "organization", "address_line1", "address_line2", "dependent_locality", "locality", "admin_area", "postal_code",
          "sorting_code")
AUTOCOMPLETE = {"name": "name", "organization": "organization", "address_line1": "address-line1", "address_line2": "address-line2",
                "dependent_locality": "address-level3", "locality": "address-level2", "admin_area": "address-level1",
                "postal_code": "postal-code", "sorting_code": "postal-code"}
MAX_LEN = {"name": 100, "organization": 100, "postal_code": 20, "sorting_code": 20}
FIELD_MAX = 200

# The label of each kind of field, by the metadata's name types (and address_local.json's own few).
LABELS = {
    "name": N_("Full name"), "organization": N_("Company"),
    "address_line1": N_("Address line 1"), "address_line2": N_("Address line 2 (apt, suite, unit)"),
    "street_main": N_("Address"), "street_detail": N_("Address details"),
    "banchi": N_("Street address (block number)"), "building": N_("Building name, room"),
    "municipality": N_("City, ward, town or village"),
    # %C
    "city": N_("City"), "post_town": N_("Town/City"), "town_city": N_("Town/City"),
    # %D
    "suburb": N_("Suburb"), "neighborhood": N_("Neighborhood"), "village_township": N_("Village/Township"), "townland": N_("Townland"),
    # %S
    "area": N_("Area"), "county": N_("County"), "department": N_("Department"), "district": N_("District"), "do_si": N_("Do/Si"),
    "emirate": N_("Emirate"), "island": N_("Island"), "oblast": N_("Oblast"), "parish": N_("Parish"), "prefecture": N_("Prefecture"),
    "province": N_("Province"), "state": N_("State"),
    # %Z
    "zip": N_("ZIP code"), "postal": N_("Postal code"), "postcode": N_("Postcode"), "pin": N_("PIN code"), "eircode": N_("Eircode"),
    "postal_jp": N_("Postal code (〒)"),
    "sorting_code": N_("Sorting code"),
}
PHONE_LABEL, NOTE_LABEL, COUNTRY_LABEL, NICKNAME_LABEL, OPTIONAL = (N_("Phone number"), N_("Delivery note"), N_("Country"), N_("Nickname"),
                                                                     N_("optional"))
CARD_NUMBER = re.compile(r"(?:\d[ -]?){12,}")


class AddressError(Exception):
    """A refusal whose message is safe to show (it names the field)."""

    def __init__(self, message: str, field: str = ""):
        super().__init__(message)
        self.field = field


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads((DATA / "address_data.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _local() -> dict[str, Any]:
    return {k: v for k, v in json.loads((DATA / "address_local.json").read_text(encoding="utf-8")).items() if k != "about"}


def countries() -> list[dict[str, str]]:
    """Every country the metadata knows: {"code", "name"} (the English name; the apps show the name in the user's language)."""
    return [{"code": cc, "name": str(r.get("name") or cc).title()} for cc, r in _data()["countries"].items()]


def known(cc: str) -> bool:
    return (cc or "").upper() in _data()["countries"]


def meta(cc: str) -> dict[str, Any]:
    """The country's metadata over the defaults (ZZ), with address_local.json on top."""
    cc = (cc or "").upper()
    rec = {**_data()["default"], **_data()["countries"].get(cc, {})}
    loc = _local().get(cc, {})
    for k in ("state_name_type", "locality_name_type", "sublocality_name_type", "zip_name_type"):
        if k in loc:
            rec[k] = loc[k]
    rec["form"] = loc.get("form") or rec.get("fmt") or "%N%n%A%n%C"
    if loc.get("fmt"):
        rec["fmt"] = loc["fmt"]   # the postal format with the fields the local form adds (shown on cards)
    rec["form_require"] = loc.get("require") or "N" + str(rec.get("require") or "")
    rec["labels"] = loc.get("labels") or {}
    return rec


def calling_code(cc: str) -> str:
    c = _data()["calling"].get((cc or "").upper())
    return f"+{c['code']}" if c else ""


def default_country(*, timezone: str = "", lang: str = "", previous: str = "") -> str:
    """The country a new address starts with: the user's last address, else their time zone's country, else the region of their
    language ("pt-BR" -> BR), else US."""
    for cc in (previous, _data()["tz"].get(timezone or "", ""), (lang.split("-")[1] if "-" in (lang or "") else "")):
        if cc and known(cc):
            return cc.upper()
    return "US"


def _tokens(fmt: str) -> list[str]:
    return [m for m in re.findall(r"%([A-Z])", fmt or "")]


def _regions(rec: dict[str, Any], lang: str) -> list[dict[str, str]]:
    keys = [k for k in str(rec.get("sub_keys") or "").split("~") if k]
    if not keys:
        return []
    names = str(rec.get("sub_names") or "").split("~")
    latin = str(rec.get("sub_lnames") or "").split("~")
    own = (lang or "").split("-")[0] == str(rec.get("lang") or "").split("-")[0]
    names = names if len(names) == len(keys) else keys        # where the keys are the local names themselves (JP, KR)
    pick = latin if not own and len(latin) == len(keys) else names
    return [{"key": k, "name": n or k} for k, n in zip(keys, pick)]


def _label_key(rec: dict[str, Any], token: str, line: int = 0) -> str:
    own = rec["labels"]
    if token == "A":
        return own.get(f"A{line}") or ("address_line1" if line == 1 else "address_line2")
    if token in own:
        return own[token]
    if token == "N":
        return "name"
    if token == "O":
        return "organization"
    if token == "C":
        return {"post_town": "post_town", "district": "district", "suburb": "suburb"}.get(rec.get("locality_name_type") or "", "city")
    if token == "D":
        return rec.get("sublocality_name_type") or "suburb"
    if token == "S":
        return rec.get("state_name_type") or "province"
    if token == "Z":
        return rec.get("zip_name_type") or "postal"
    return "sorting_code"


def form(cc: str, lang: str | None = None) -> dict[str, Any]:
    """The address form of one country, ready to render (see the module docstring and the SHIPPING API in server.py)."""
    cc = (cc or "").upper()
    if not known(cc):
        raise AddressError(f"unknown country code {cc!r}", "country")
    rec = meta(cc)
    req = set(rec["form_require"])
    fields: list[dict[str, Any]] = []
    order = list(dict.fromkeys(_tokens(rec["form"])))
    order = ["N", *(x for x in order if x != "N")]   # a form starts with who it goes to, whatever the envelope order
    for tok in order:
        if tok == "O":
            continue   # shops rarely ask for a company; a profile may still hold one
        if tok == "A":
            for line in (1, 2):
                key = f"address_line{line}"
                fields.append({"key": key, "label": t(LABELS[_label_key(rec, "A", line)], lang=lang), "required": line == 1 and "A" in req,
                               "autocomplete": AUTOCOMPLETE[key]})
            continue
        key = TOKENS.get(tok)
        if key is None:
            continue
        f: dict[str, Any] = {"key": key, "label": t(LABELS[_label_key(rec, tok)], lang=lang), "required": tok in req,
                             "autocomplete": AUTOCOMPLETE[key]}
        if tok == "S" and (opts := _regions(rec, lang or "")):
            f["options"] = opts
        if tok == "Z":
            if rec.get("zip"):
                f["pattern"] = f"^(?:{rec['zip']})$"
            ex = str(rec.get("zipex") or "").split(",")[0]
            if ex:
                f["example"] = ex
            f["numeric"] = bool(ex) and bool(re.fullmatch(r"[\d\- ]+", ex))
        fields.append(f)
    return {"country": cc, "country_name": str(rec.get("name") or cc).title(), "lang": rec.get("lang") or "", "fields": fields,
            "phone": {"label": t(PHONE_LABEL, lang=lang), "calling_code": calling_code(cc), "required": True},
            "note": {"label": t(NOTE_LABEL, lang=lang), "required": False},
            "labels": {"country": t(COUNTRY_LABEL, lang=lang), "nickname": t(NICKNAME_LABEL, lang=lang), "optional": t(OPTIONAL, lang=lang)},
            "format": rec.get("fmt") or "%N%n%A%n%C"}


def phone_e164(raw: str, cc: str) -> str:
    """A phone number as E.164 ("+821012345678"): "+..." and "00..." as they are; else the national prefix is dropped and the
    country's calling code put in front."""
    text = (raw or "").strip()
    if not text:
        return ""
    if re.search(r"[^\d+\-().\s/]", text):
        raise AddressError("the phone number may have only digits, spaces, dashes, brackets and a leading +", "phone")
    digits = re.sub(r"\D", "", text)
    if text.startswith("+"):
        out = digits
    elif digits.startswith("00"):
        out = digits[2:]
    else:
        c = _data()["calling"].get((cc or "").upper())
        if not c:
            raise AddressError("write the phone number with its country code (+...)", "phone")
        prefix = c.get("prefix", "")
        national = digits[len(prefix):] if prefix and digits.startswith(prefix) and len(digits) > 7 else digits
        out = c["code"] + national
    if not 8 <= len(out) <= 15:
        raise AddressError("the phone number does not look complete", "phone")
    return "+" + out


def phone_national(e164: str, cc: str) -> str:
    """The national way to dial an E.164 number of that country ("+821012345678" -> "01012345678"), for forms that want it."""
    c = _data()["calling"].get((cc or "").upper())
    digits = re.sub(r"\D", "", e164 or "")
    if c and digits.startswith(c["code"]):
        return c.get("prefix", "") + digits[len(c["code"]):]
    return digits


def _text(value: Any, key: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise AddressError(f"{key} must be text", key)
    v = " ".join(value.split())
    if len(v) > MAX_LEN.get(key, FIELD_MAX):
        raise AddressError(f"{key} is too long", key)
    if CARD_NUMBER.search(v) and key != "phone":
        raise AddressError(f"{key} must not contain a card number", key)
    return v


def clean(body: dict[str, Any], *, partial: dict[str, Any] | None = None) -> dict[str, Any]:
    """A checked address: {"country", the field keys, "phone" (E.164)}. `partial`: the stored address a change applies to.
    Required fields, the postal-code pattern and the region list follow the address's country, never the app's language."""
    base = dict(partial or {})
    cc = str(body.get("country") if body.get("country") is not None else base.get("country") or "").strip().upper()
    if not known(cc):
        raise AddressError("country must be a two-letter country code (e.g. US, KR, JP)", "country")
    out: dict[str, Any] = {"country": cc}
    for key in FIELDS:
        out[key] = _text(body[key], key) if key in body else str(base.get(key) or "")
    rec = meta(cc)
    f = form(cc)
    for field in f["fields"]:
        key, val = field["key"], out[field["key"]]
        if field["required"] and not val:
            raise AddressError(f"{key} is required for an address in {cc}", key)
        if not val:
            continue
        if key == "postal_code" and field.get("pattern"):
            if not re.fullmatch(field["pattern"][1:-1], val, re.I):
                raise AddressError(f"postal_code {val!r} is not a postal code of {cc} (e.g. {field.get('example', '')})", key)
            if re.search(r"[a-z]", val) and re.search(r"[A-Z]", rec.get("zip") or ""):
                val = val.upper()
        if key == "admin_area" and field.get("options"):
            hit = _region_key(rec, val)
            if hit is None:
                raise AddressError(f"admin_area {val!r} is not one of the regions of {cc}", key)
            val = hit
        out[key] = val
    phone_raw = body.get("phone") if "phone" in body else base.get("phone")
    out["phone"] = phone_e164(_text(phone_raw, "phone"), cc) if phone_raw else ""
    if not out["phone"]:
        raise AddressError("phone is required", "phone")
    return out


def _region_key(rec: dict[str, Any], value: str) -> str | None:
    keys = [k for k in str(rec.get("sub_keys") or "").split("~") if k]
    want = value.casefold()
    for lst in (keys, str(rec.get("sub_names") or "").split("~"), str(rec.get("sub_lnames") or "").split("~")):
        if len(lst) == len(keys):
            for k, n in zip(keys, lst):
                if n and n.casefold() == want:
                    return k
    return None


def formatted(addr: dict[str, Any], *, with_name: bool = True) -> str:
    """The address in its country's own postal format (the metadata's fmt), one line per line of the format, empty parts left out."""
    cc = str(addr.get("country") or "").upper()
    rec = meta(cc)
    upper = set(str(rec.get("upper") or ""))
    fmt = str(rec.get("fmt") or "%N%n%O%n%A%n%C")

    def value(tok: str) -> str:
        if tok == "A":
            return "\n".join(str(x) for x in (addr.get("address_line1"), addr.get("address_line2")) if x)
        if tok == "N" and not with_name:
            return ""
        v = str(addr.get(TOKENS.get(tok, ""), "") or "")
        return v.upper() if tok in upper else v

    lines: list[str] = []
    for raw in fmt.split("%n"):
        toks = _tokens(raw)
        if not toks or not any(value(tok) for tok in toks):
            continue
        line = re.sub(r"%([A-Z])", lambda m: value(m.group(1)), raw)
        for part in line.split("\n"):
            part = re.sub(r"\s*,(\s*,)+", ",", part)
            part = re.sub(r"[ \t]{2,}", " ", part).strip().strip(",").strip()
            if part:
                lines.append(part)
    return "\n".join(lines)


def one_line(addr: dict[str, Any]) -> str:
    return ", ".join(formatted(addr, with_name=False).split("\n"))

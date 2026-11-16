"""Rebuild server/src/assistant/data/address_data.json: the open address metadata the shipping forms are built from.

Sources (see NOTICE):
- Google's address metadata (the data behind libaddressinput / Chromium's address forms), CC-BY 4.0:
  https://chromium-i18n.appspot.com/ssl-address/data/<CC> -- field order (fmt / lfmt), required fields, postal-code pattern, the
  names of the fields (state / province / prefecture ..., zip / postal / pin ...) and the first-level sub-region lists.
- libphonenumber's PhoneNumberMetadata.xml, Apache 2.0: the calling code and national prefix of each region (to store phones as E.164).
- The IANA time zone table (zone.tab, public domain): a time zone's country, for the default country of a new address.

Run by hand (needs the internet): `python scripts/fetch_address_data.py`. The app never fetches anything at run time.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://chromium-i18n.appspot.com/ssl-address/data"
PHONE = "https://raw.githubusercontent.com/google/libphonenumber/master/resources/PhoneNumberMetadata.xml"
ZONES = "https://data.iana.org/time-zones/tzdb/zone.tab"
OUT = Path(__file__).resolve().parents[1] / "server" / "src" / "assistant" / "data" / "address_data.json"
KEEP = ("name", "lang", "languages", "fmt", "lfmt", "require", "upper", "zip", "zipex", "state_name_type", "locality_name_type",
        "sublocality_name_type", "zip_name_type", "sub_keys", "sub_names", "sub_lnames")


def get(url: str, tries: int = 4) -> str:
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return r.read().decode("utf-8")
        except Exception:  # noqa: BLE001
            if i == tries - 1:
                raise
            time.sleep(1 + i)
    return ""


def main() -> int:
    countries = json.loads(get(BASE))["countries"].split("~")

    def one(cc: str) -> tuple[str, dict]:
        rec = json.loads(get(f"{BASE}/{cc}"))
        return cc, {k: rec[k] for k in KEEP if k in rec}

    with ThreadPoolExecutor(8) as pool:
        recs = dict(pool.map(one, ["ZZ", *countries]))
    xml = get(PHONE)
    calling: dict[str, dict[str, str]] = {}
    for m in re.finditer(r"<territory\b([^>]*)>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        cc, code = attrs.get("id", ""), attrs.get("countryCode", "")
        if re.fullmatch(r"[A-Z]{2}", cc) and code and cc not in calling:
            calling[cc] = {"code": code, **({"prefix": attrs["nationalPrefix"]} if attrs.get("nationalPrefix") else {})}
    tz: dict[str, str] = {}
    for line in get(ZONES).splitlines():
        if line and not line.startswith("#"):
            parts = line.split("\t")
            if len(parts) >= 3:
                tz[parts[2]] = parts[0]
    data = {"about": {"address": f"{BASE} (Google, CC-BY 4.0)", "phone": f"{PHONE} (Apache 2.0)", "tz": f"{ZONES} (public domain)",
                      "fetched": time.strftime("%Y-%m-%d")},
            "default": recs.pop("ZZ"), "countries": dict(sorted(recs.items())), "calling": dict(sorted(calling.items())),
            "tz": dict(sorted(tz.items()))}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=False) + "\n", encoding="utf-8")
    print(f"{len(data['countries'])} countries, {len(calling)} calling codes, {len(tz)} zones -> {OUT} ({OUT.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

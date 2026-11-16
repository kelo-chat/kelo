"""The page the agent's browser shows in the store screenshots' live view: a hotel comparison on stay.example.com (an example
domain, no real brand). The agent's browser is a desktop window (docker/entrypoint.sh: --window-size=1440,900), so the page is a
desktop layout, 1440x900 CSS pixels. Writes tools/store/data/site-{en,ko}.jpg, read by store_mock.py.

    uv run python tools/store/site_frame.py
"""
import pathlib

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "tools/store/data"
FONT = (ROOT / "apps/android/design/promo-src/fonts/PretendardVariable.woff2").as_uri()

D = {
    "ko": dict(q="부산 해운대 · 11월 17일 – 19일 · 2박 · 성인 1명", sort="가격 낮은 순", n="숙소 3곳", per="1박", btn="자세히", tax="세금 포함",
               rows=[("오션뷰 호텔 해운대", "해운대역 도보 4분 · 바다 전망", "4.6", "128,000원", "#7FB3D5"),
                     ("마린 스테이 부산", "해운대해수욕장 앞 · 조식 포함", "4.4", "142,000원", "#A3C4A8"),
                     ("센텀 비즈니스 호텔", "센텀시티역 도보 2분 · 회의실", "4.3", "96,000원", "#D9B38C")]),
    "en": dict(q="Boston, Back Bay · Nov 17 – 19 · 2 nights · 1 adult", sort="Lowest price", n="3 places", per="night", btn="Details",
               tax="incl. taxes",
               rows=[("Harbor View Hotel", "4 min walk to the station · Water view", "4.6", "$189", "#7FB3D5"),
                     ("Back Bay Stay", "Near Copley Square · Breakfast included", "4.4", "$214", "#A3C4A8"),
                     ("Seaport Business Inn", "2 min to South Station · Meeting rooms", "4.3", "$159", "#D9B38C")]),
}

CSS = """@font-face{font-family:P;src:url('%s')}*{box-sizing:border-box;margin:0}
body{font-family:P;background:#f3f5f8;color:#1b2430;width:1440px;height:900px;overflow:hidden}
.top{background:#16508a;color:#fff;padding:22px 48px;display:flex;align-items:center;gap:40px}
.logo{font-weight:800;font-size:34px;letter-spacing:-.5px} .logo span{opacity:.7;font-weight:500}
.q{flex:1;background:#fff;color:#1b2430;border-radius:12px;padding:16px 20px;font-size:24px}
.bar{display:flex;justify-content:space-between;padding:20px 48px 14px;font-size:22px;color:#56606e} .bar b{color:#16508a}
.grid{display:flex;gap:24px;padding:0 48px}
.card{flex:1;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 1px 4px #0002}
.ph{height:260px} .b{padding:20px 22px 22px} .t{font-weight:700;font-size:30px} .s{font-size:21px;color:#68717d;margin-top:6px}
.r{display:flex;justify-content:space-between;align-items:flex-end;margin-top:18px}
.st{background:#e7f0fa;color:#16508a;font-weight:700;font-size:22px;padding:4px 10px;border-radius:8px}
.pr{text-align:right;font-size:20px;color:#56606e;line-height:1.3} .pr b{font-size:32px;color:#1b2430} small{font-size:17px}
.btn{margin-top:18px;background:#f08a24;color:#fff;text-align:center;font-weight:700;border-radius:10px;padding:14px;font-size:24px}"""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
        for lang, d in D.items():
            rows = "".join(
                f"""<div class=card><div class=ph style="background:linear-gradient(135deg,{c},#ffffff55)"></div><div class=b>
                <div class=t>{t}</div><div class=s>{s}</div><div class=r><span class=st>★ {r}</span>
                <span class=pr><b>{pr}</b> / {d['per']}<br><small>{d['tax']}</small></span></div><div class=btn>{d['btn']}</div></div></div>"""
                for t, s, r, pr, c in d["rows"])
            html = (f"<!doctype html><meta charset=utf-8><style>{CSS % FONT}</style><div class=top><div class=logo>stay<span>.example</span>"
                    f"</div><div class=q>{d['q']}</div></div><div class=bar><span>{d['n']}</span><b>{d['sort']} ▾</b></div>"
                    f"<div class=grid>{rows}</div>")
            f = OUT / f"_site-{lang}.html"
            f.write_text(html)
            pg.goto(f.as_uri())
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(OUT / f"site-{lang}.jpg"), type="jpeg", quality=90)
            f.unlink()
            print(OUT / f"site-{lang}.jpg")
        b.close()


if __name__ == "__main__":
    main()

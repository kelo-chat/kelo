"""Store screenshots from raw captures: the app's screen in a device frame on a store-sized canvas, one short headline above it.

    uv run python tools/store/compose.py ios     RAW_DIR ios/store/screenshots
    uv run python tools/store/compose.py android RAW_DIR android/play/screenshots

RAW_DIR holds {en,ko}-NN.png, NN = 01..08 as in SHOTS, captured at the device's own resolution (no scaling before this step):
- ios: 1206x2622, the iPhone 17 simulator (kelo-ios-check), from ios/Kelo/UITests/KeloStoreShotsTests.swift;
- android: 1080x2340, the emulator set to a Galaxy S size (`wm size 1080x2340`, density 420), from tools/store/android_shots.sh.
Writes ios: OUT/{en,ko}/{6.9,6.5}/NN-name.png (1320x2868 and 1284x2778); android: OUT/{en,ko}/NN-name.png (1080x2160).
Every PNG is RGB without alpha (App Store Connect refuses transparency; Play wants 24-bit PNG).

The capture is only ever scaled uniformly (one factor for width and height, Lanczos), never stretched or cropped. Frames are
drawn here, generic and without any logo: an iPhone-like frame (thin even bezel, Dynamic Island, the display's own corner radius)
and a Galaxy-S-like frame (thin even bezel, a centred punch-hole camera, flatter corners). Same look as android/design/
play_screenshots.py and feature_graphic.py: Pretendard ExtraBold headline on the cream gradient.
"""

from __future__ import annotations

import pathlib
import sys
import tempfile

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[2]
FONT = (ROOT / "android/design/promo-src/fonts/PretendardVariable.woff2").as_uri()

SHOTS = [  # (NN-name, ko headline, en headline): short, and true to what the shot shows
    ("01-handoff", "채팅으로 일을 맡겨요", "Hand off tasks in a chat"),
    ("02-approval", "무엇을 결제할지 내가 정해요", "You decide what it pays for"),
    ("03-live", "브라우저 작업을 실시간으로 봐요", "Watch its browser work live"),
    ("04-briefing", "아침마다 브리핑을 받아요", "A briefing every morning"),
    ("05-goal", "다짐을 세우면 계획을 함께 짜요", "Plan your goals together"),
    ("06-connectors", "쓰는 서비스를 연결해요", "Connect the services you use"),
    ("07-privacy", "내 정보가 머무는 곳을 알려 줘요", "See where your data lives"),
    ("08-tone", "말투를 골라요", "Choose how it talks to you"),
]

# Device geometry in the capture's own pixels.
DEVICES = {
    # iPhone 17: 1206x2622 at 3x; display corners ~62 pt; Dynamic Island 126x37 pt, 11 pt from the top; thin even black border
    # inside a narrow metal band; action button and volume on the left, side button and Camera Control on the right.
    "ios": {"screen": (1206, 2622), "radius": 186, "bezel": 30, "band": 9,
            "band_css": "linear-gradient(135deg,#d9dce1 0%,#a9adb4 22%,#e8eaee 50%,#9ea3aa 78%,#cfd2d7 100%)",
            "cutout": {"kind": "island", "w": 378, "h": 112, "top": 33},
            "buttons": [("left", 560, 105), ("left", 760, 200), ("left", 1010, 200), ("right", 860, 300), ("right", 1660, 170)]},
    # Galaxy S class: 1080x2340; flatter corners; a small centred punch-hole in the status bar; volume and side key on the right.
    "android": {"screen": (1080, 2340), "radius": 92, "bezel": 26, "band": 8,
                "band_css": "linear-gradient(135deg,#4a4e55 0%,#2b2e33 30%,#5b6068 55%,#2a2d31 80%,#474b52 100%)",
                "cutout": {"kind": "hole", "d": 40, "cy": 60},
                "buttons": [("right", 560, 300), ("right", 960, 170)]},
}

CANVASES = {"ios": [("6.9", 1320, 2868), ("6.5", 1284, 2778)], "android": [(None, 1080, 2160)]}
# headline band: Play asks taglines to take no more than 20% of the image (this band is ~16%, the text itself less)
BAND = {"ios": 0.15, "android": 0.155}

PAGE = """<!doctype html><html><head><meta charset=utf-8><style>
@font-face{{font-family:P;src:url('{font}') format('woff2');font-weight:100 900}}
*{{margin:0;box-sizing:border-box}} html,body{{width:{W}px;height:{H}px;overflow:hidden}}
body{{background:linear-gradient(180deg,#FFF6DD 0%,#FDE7B0 100%);font-family:P;position:relative}}
h1{{position:absolute;left:{pad}px;right:{pad}px;top:0;height:{band}px;display:flex;align-items:center;justify-content:center;
   text-align:center;font-size:{fs}px;font-weight:800;color:#1B2530;letter-spacing:{ls}px;line-height:1.18;word-break:keep-all;
   overflow-wrap:anywhere}}
.dev{{position:absolute;left:{x}px;top:{y}px;width:{ow}px;height:{oh}px}}
.btn{{position:absolute;background:{btn};border-radius:{br}px}}
.band{{position:absolute;inset:0;border-radius:{ro}px;background:{band_css};
   box-shadow:0 {sh1}px {sh2}px rgba(120,80,0,.20),0 {sh3}px {sh4}px rgba(0,0,0,.12)}}
.bezel{{position:absolute;inset:{bd}px;border-radius:{rb}px;background:#0b0c0e}}
.screen{{position:absolute;left:{sx}px;top:{sy}px;width:{sw}px;height:{shh}px;border-radius:{rs}px;overflow:hidden;background:#000}}
.screen img{{display:block;width:{sw}px;height:{shh}px}}
.cut{{position:absolute;background:#050506}}
</style></head><body><h1>{caption}</h1><div class=dev>{buttons}<div class=band></div><div class=bezel></div>
<div class=screen><img src="{shot}"><div class=cut style="{cut}"></div></div></div></body></html>"""


def layout(kind: str, W: int, H: int) -> dict:
    d = DEVICES[kind]
    sw0, sh0 = d["screen"]
    edge = d["bezel"] + d["band"]
    ow0, oh0 = sw0 + 2 * edge, sh0 + 2 * edge
    band = round(H * BAND[kind])
    bottom = round(H * 0.035)
    s = min((H - band - bottom) / oh0, (W * 0.86) / ow0)   # one factor for both directions
    sw, sh = round(sw0 * s), round(sh0 * s)
    e = lambda v: max(1, round(v * s))
    ow, oh = sw + 2 * (e(d["bezel"]) + e(d["band"])), sh + 2 * (e(d["bezel"]) + e(d["band"]))
    return {"s": s, "sw": sw, "sh": sh, "ow": ow, "oh": oh, "x": (W - ow) // 2, "y": band, "band": band, "e": e}


def render(kind: str, raw: pathlib.Path, out: pathlib.Path) -> list[pathlib.Path]:
    d = DEVICES[kind]
    made = []
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="kelo-store-"))
    with sync_playwright() as p:
        b = p.chromium.launch()
        for lang in ("en", "ko"):
            for i, (name, ko, en) in enumerate(SHOTS, 1):
                src = raw / f"{lang}-{i:02d}.png"
                if not src.exists():
                    print("missing", src)
                    continue
                cap = Image.open(src).convert("RGB")
                if cap.size != d["screen"]:
                    raise SystemExit(f"{src}: {cap.size}, expected {d['screen']} (capture at the device's own resolution)")
                for size, W, H in CANVASES[kind]:
                    L = layout(kind, W, H)
                    e = L["e"]
                    shot = tmp / f"{lang}-{i:02d}-{L['sw']}.png"
                    cap.resize((L["sw"], L["sh"]), Image.LANCZOS).save(shot)
                    bd, bz = e(d["band"]), e(d["bezel"])
                    rs = e(d["radius"])
                    c = d["cutout"]
                    if c["kind"] == "island":
                        cw, ch = e(c["w"]), e(c["h"])
                        cut = f"left:{(L['sw'] - cw) // 2}px;top:{e(c['top'])}px;width:{cw}px;height:{ch}px;border-radius:{ch / 2}px"
                    else:
                        dd = e(c["d"])
                        cut = f"left:{(L['sw'] - dd) // 2}px;top:{e(c['cy']) - dd // 2}px;width:{dd}px;height:{dd}px;border-radius:50%"
                    btns = []
                    for side, top, length in d["buttons"]:
                        t = bd + bz + e(top)
                        w = max(3, e(10))
                        x = -w + 1 if side == "left" else L["ow"] - 1
                        btns.append(f'<div class=btn style="left:{x}px;top:{t}px;width:{w}px;height:{e(length)}px"></div>')
                    caption = ko if lang == "ko" else en
                    fs = round(W * (0.068 if kind == "ios" else 0.064))
                    if len(caption) > (16 if lang == "ko" else 24):
                        fs = round(fs * 0.92)
                    html = PAGE.format(
                        font=FONT, W=W, H=H, pad=round(W * 0.07), band=L["band"], fs=fs, ls=round(-fs * 0.022, 1), caption=caption,
                        x=L["x"], y=L["y"], ow=L["ow"], oh=L["oh"], btn=d["band_css"], br=max(2, e(4)),
                        ro=rs + bz + bd, band_css=d["band_css"], sh1=e(40), sh2=e(90), sh3=e(8), sh4=e(24),
                        bd=bd, rb=rs + bz, sx=bd + bz, sy=bd + bz, sw=L["sw"], shh=L["sh"], rs=rs, shot=shot.as_uri(), cut=cut,
                        buttons="".join(btns))
                    page = tmp / f"{lang}-{i:02d}-{W}.html"
                    page.write_text(html)
                    pg = b.new_page(viewport={"width": W, "height": H}, device_scale_factor=1)
                    pg.goto(page.as_uri())
                    pg.evaluate("document.fonts.ready")
                    pg.wait_for_timeout(200)
                    dest = out / lang / size / f"{name}.png" if size else out / lang / f"{name}.png"
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    pg.screenshot(path=str(dest))
                    pg.close()
                    Image.open(dest).convert("RGB").save(dest, optimize=True)   # no alpha channel
                    made.append(dest)
                    print(dest, Image.open(dest).size)
        b.close()
    for f in tmp.iterdir():
        f.unlink()
    tmp.rmdir()
    return made


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in DEVICES:
        raise SystemExit(__doc__)
    render(sys.argv[1], pathlib.Path(sys.argv[2]).resolve(), pathlib.Path(sys.argv[3]).resolve())

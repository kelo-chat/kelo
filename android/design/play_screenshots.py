# Google Play phone screenshots (1080x1920): a caption on top and the raw phone shot (1080x1920) inside a device frame.
#   uv run python android/design/play_screenshots.py RAW_DIR android/play/screenshots
# RAW_DIR holds ko-01.png .. ko-08.png and en-01.png .. en-08.png (adb screencap of a 1080x1920 emulator, demo-mode status bar);
# writes OUT/ko/01-chat.png .. OUT/en/08-connect.png. Same look as feature_graphic.py (Pretendard, cream background).
import pathlib
import sys

from playwright.sync_api import sync_playwright

SHOTS = [  # (file name, ko caption, en caption)
    ("01-chat", "채팅으로 일을 맡겨요", "Hand off tasks in a chat"),
    ("02-agent", "맡긴 일이 어디까지 왔는지 한눈에", "See how far each task has come"),
    ("03-work", "결제·전송은 꼭 먼저 물어봐요", "It asks before paying or sending"),
    ("04-live", "브라우저 작업을 실시간으로 봐요", "Watch the agent's browser live"),
    ("05-connectors", "쓰는 서비스를 연결해요", "Connect the services you use"),
    ("06-goals", "다짐을 적어 두고 함께 챙겨요", "Keep track of your goals"),
    ("07-privacy", "내 정보가 어디에 있는지 알려 줘요", "See where your data is kept"),
    ("08-connect", "내 Kelo 서버에 QR로 연결해요", "Pair with your own Kelo server"),
]

root = pathlib.Path(__file__).resolve().parents[2]
raw, out = pathlib.Path(sys.argv[1]).resolve(), pathlib.Path(sys.argv[2]).resolve()
font = (root / "android/design/promo-src/fonts/PretendardVariable.woff2").resolve().as_uri()

PAGE = """<!doctype html><html><head><meta charset=utf-8><style>
@font-face{{font-family:P;src:url('{font}') format('woff2');font-weight:100 900}}
*{{margin:0;box-sizing:border-box}} html,body{{width:1080px;height:1920px;overflow:hidden}}
body{{background:linear-gradient(180deg,#FFF6DD 0%,#FDE7B0 100%);font-family:P}}
h1{{position:absolute;left:60px;right:60px;top:0;height:300px;display:flex;align-items:center;justify-content:center;text-align:center;
   font-size:{size}px;font-weight:800;color:#1B2530;letter-spacing:-1.5px;line-height:1.2;word-break:keep-all;overflow-wrap:anywhere}}
.phone{{position:absolute;left:120px;top:330px;width:852px;height:1505px;border-radius:50px;background:#16191D;padding:6px;
   box-shadow:0 30px 60px rgba(120,80,0,.18),0 6px 16px rgba(0,0,0,.10)}}
.phone img{{display:block;width:840px;height:1493px;border-radius:44px}}
</style></head><body><h1>{caption}</h1><div class=phone><img src="{shot}"></div></body></html>"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1080, "height": 1920})
    for lang in ("ko", "en"):
        (out / lang).mkdir(parents=True, exist_ok=True)
        for i, (name, ko, en) in enumerate(SHOTS, 1):
            src = raw / f"{lang}-{i:02d}.png"
            caption = ko if lang == "ko" else en
            html = PAGE.format(font=font, caption=caption, shot=src.as_uri(), size=64 if len(caption) < 30 else 58)
            f = raw / f"_frame-{lang}-{i:02d}.html"
            f.write_text(html)
            pg.goto(f.as_uri())
            pg.evaluate("document.fonts.ready")
            pg.wait_for_timeout(250)
            pg.screenshot(path=str(out / lang / f"{name}.png"))
            f.unlink()
            print(out / lang / f"{name}.png")
    b.close()

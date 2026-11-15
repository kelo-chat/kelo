# Play feature graphics (1024x500): uv run python android/design/feature_graphic.py . android/play/graphics
import sys, pathlib
from playwright.sync_api import sync_playwright
root = pathlib.Path(sys.argv[1]); out = pathlib.Path(sys.argv[2])
svg = (root/"android/design/character/kelo_idle.svg").read_text()
font = (root/"android/design/promo-src/fonts/PretendardVariable.woff2").resolve().as_uri()
lines = {"ko": "직접 설치해 쓰는<br>나만의 AI 비서", "en": "Your own AI assistant,<br>on your own server"}
for lang, line in lines.items():
    html = f"""<!doctype html><html><head><meta charset=utf-8><style>
@font-face{{font-family:P;src:url('{font}') format('woff2');font-weight:100 900}}
*{{margin:0}}html,body{{width:1024px;height:500px}}
body{{background:linear-gradient(180deg,#FFF6DD 0%,#FDE7B0 100%);font-family:P;display:flex;align-items:center}}
.m{{width:330px;height:330px;margin-left:150px}} .m svg *{{animation:none!important}}
.t{{margin-left:60px}} h1{{font-size:150px;font-weight:800;color:#0A74C4;letter-spacing:-4px;line-height:1}}
p{{margin-top:26px;font-size:34px;font-weight:600;color:#1F2933;line-height:1.35;letter-spacing:-0.5px}}
</style></head><body><div class=m>{svg}</div><div class=t><h1>Kelo</h1><p>{line}</p></div></body></html>"""
    with sync_playwright() as p:
        b = p.chromium.launch(); pg = b.new_page(viewport={"width":1024,"height":500})
        f=out/f'fg-{lang}.html'; f.write_text(html); pg.goto(f.resolve().as_uri()); pg.evaluate('document.fonts.ready'); pg.wait_for_timeout(400)
        pg.screenshot(path=str(out/f"feature-graphic-{lang}.png")); b.close()

import sys, os
from playwright.sync_api import sync_playwright
# usage: snap.py lang fmt scene(or -) out_prefix t1 t2 ...
lang, fmt, scene, out = sys.argv[1:5]; ts=[float(x) for x in sys.argv[5:]]
SZ={'h':(1920,1080),'v':(1080,1920),'gl':(1600,900),'gp':(1200,1500)}
w,h=SZ[fmt]; P=os.path.dirname(os.path.abspath(__file__))
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':w,'height':h})
    errs=[]; pg.on('pageerror', lambda e: errs.append(str(e))); pg.on('console', lambda m: errs.append(m.text) if m.type=='error' else None)
    q=f'?lang={lang}&fmt={fmt}'+(f'&scene={scene}' if scene!='-' else '')
    pg.goto(f'file://{P}/ad.html{q}'); pg.evaluate('window.READY')
    print('dur', pg.evaluate('window.DURATION'), pg.evaluate('window.SCENES'))
    for t in ts:
        pg.evaluate(f'render({t})'); pg.screenshot(path=f'{out}_{t:05.2f}.png')
    print('errors', errs); b.close()

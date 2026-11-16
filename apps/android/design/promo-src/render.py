import sys, os, subprocess, time
from playwright.sync_api import sync_playwright
# usage: render.py lang fmt scene(or -) fps out (out .mp4 => video-only h264; out dir => png frames)
lang, fmt, scene, fps, out = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
SZ={'h':(1920,1080),'v':(1080,1920),'gl':(1600,900),'gp':(1200,1500)}
w,h=SZ[fmt]; P=os.path.dirname(os.path.abspath(__file__))
with sync_playwright() as p:
    b=p.chromium.launch(); pg=b.new_page(viewport={'width':w,'height':h})
    errs=[]; pg.on('pageerror', lambda e: errs.append(str(e)))
    q=f'?lang={lang}&fmt={fmt}'+(f'&scene={scene}' if scene!='-' else '')
    pg.goto(f'file://{P}/ad.html{q}'); pg.evaluate('window.READY')
    dur=pg.evaluate('window.DURATION'); n=int(round(dur*fps))
    vid = out.endswith('.mp4')
    if vid:
        ff=subprocess.Popen(['ffmpeg','-loglevel','error','-y','-f','image2pipe','-framerate',str(fps),'-c:v','png','-i','-','-c:v','libx264','-preset','slow','-crf','16','-pix_fmt','yuv420p','-movflags','+faststart',out],stdin=subprocess.PIPE)
    else: os.makedirs(out, exist_ok=True)
    t0=time.time()
    for i in range(n):
        pg.evaluate(f'render({i/fps})')
        png=pg.screenshot(type='png')
        if vid: ff.stdin.write(png)
        else: open(f'{out}/f{i:05d}.png','wb').write(png)
    if vid: ff.stdin.close(); ff.wait()
    print(f'{out}: {n} frames, {dur}s, {time.time()-t0:.0f}s render, errors={errs}')
    b.close()

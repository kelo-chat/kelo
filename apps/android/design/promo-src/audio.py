"""Original soundtrack for the Kelo promo, synthesised from scratch (no samples, no third-party music).
usage: audio.py fmt out.wav     fmt = h | v  (scene list from plans.js)
"""
import sys, numpy as np
from scipy.signal import lfilter, fftconvolve, butter

SR = 48000
rng = np.random.default_rng(7)
import json, os
# scene order and lengths come from plans.js, the same file the picture uses
_pj = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'plans.js'), encoding='utf-8').read()
PLANS = json.loads(_pj[_pj.index('{'):_pj.rindex('}') + 1])
fmt, out = sys.argv[1], sys.argv[2]
plan = [tuple(x) for x in PLANS[fmt]]
starts = {}; acc = 0
for n, d in plan: starts[n] = acc; acc += d
DUR = acc
N = int(DUR * SR) + SR
L = np.zeros(N); R = np.zeros(N)
FX_L = np.zeros(N); FX_R = np.zeros(N)

def tt(d): return np.arange(int(d * SR)) / SR
def add(sig, at, gain=1.0, pan=0.0, fx=0.0):
    i = int(at * SR)
    if i >= N: return
    sig = sig[: N - i]
    gl, gr = np.cos((pan + 1) * np.pi / 4), np.sin((pan + 1) * np.pi / 4)
    L[i:i + len(sig)] += sig * gain * gl * 1.414; R[i:i + len(sig)] += sig * gain * gr * 1.414
    if fx:
        FX_L[i:i + len(sig)] += sig * gain * fx * gl; FX_R[i:i + len(sig)] += sig * gain * fx * gr
def hz(m): return 440.0 * 2 ** ((m - 69) / 12)
def bp(x, lo, hi, order=2):
    b, a = butter(order, [lo / (SR / 2), hi / (SR / 2)], btype='band'); return lfilter(b, a, x)
def lp(x, f, order=2):
    b, a = butter(order, f / (SR / 2)); return lfilter(b, a, x)
def hp(x, f, order=2):
    b, a = butter(order, f / (SR / 2), btype='high'); return lfilter(b, a, x)
def env(t, a, d):
    e = np.minimum(1, t / max(a, 1e-4)); return e * np.exp(-np.maximum(0, t - a) / d)

# ---------- instruments ----------
def pluck(m, d=0.9):
    t = tt(d); f = hz(m)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t / 0.12) + 0.12 * np.sin(2 * np.pi * 3.01 * f * t) * np.exp(-t / 0.06)
    return s * env(t, 0.004, 0.32)
def pad(notes, d, att=0.7, rel=0.9):
    t = tt(d + rel); s = np.zeros_like(t)
    for m in notes:
        for det in (-0.0018, 0.0, 0.0021):
            f = hz(m) * (1 + det)
            for k in range(1, 7):
                s += np.sin(2 * np.pi * f * k * t + rng.uniform(0, 6.28)) / (k ** 1.6)
    s = lp(s, 2400)
    e = np.minimum(1, t / att) * np.where(t > d, np.exp(-(t - d) / (rel / 3)), 1)
    return s * e / (len(notes) * 3)
def kick():
    t = tt(0.45); f = 46 + 95 * np.exp(-t / 0.035); ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * env(t, 0.002, 0.2) + 0.15 * hp(rng.standard_normal(len(t)), 3000) * np.exp(-t / 0.004)
def hat():
    t = tt(0.08); return hp(rng.standard_normal(len(t)), 7000) * np.exp(-t / 0.018)
def snap():
    t = tt(0.25); return bp(rng.standard_normal(len(t)), 1200, 6000) * np.exp(-t / 0.05) * 0.6 + np.sin(2 * np.pi * 190 * t) * np.exp(-t / 0.04) * 0.4
def whoosh(d=0.7, up=True):
    t = tt(d); n = rng.standard_normal(len(t)); out = np.zeros_like(t)
    seg = 512
    for i in range(0, len(t), seg):
        x = i / len(t); fc = (400 + 5000 * x) if up else (5000 - 4200 * x)
        out[i:i + seg] = bp(n[max(0, i - 2048):i + seg], fc * 0.7, min(fc * 1.4, 20000))[-len(n[i:i + seg]):]
    shape = np.sin(np.pi * np.clip(t / d, 0, 1)) ** 2
    return out * shape
def pop(f0=700, f1=1300, d=0.12):
    t = tt(d); f = f0 + (f1 - f0) * (1 - np.exp(-t / 0.02)); ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * env(t, 0.002, 0.035)
def tick(f=2600):
    t = tt(0.05); return (np.sin(2 * np.pi * f * t) * 0.6 + hp(rng.standard_normal(len(t)), 4000) * 0.4) * np.exp(-t / 0.008)
def key():
    t = tt(0.04); return bp(rng.standard_normal(len(t)), 1500, 7000) * np.exp(-t / 0.006) * rng.uniform(0.6, 1.0)
def chime(ms=(88, 95), d=1.6):
    t = tt(d); s = np.zeros_like(t)
    for j, m in enumerate(ms):
        f = hz(m); tj = np.clip(t - j * 0.07, 0, None); on = (t >= j * 0.07)
        s += on * (np.sin(2 * np.pi * f * tj) + 0.25 * np.sin(2 * np.pi * 2.76 * f * tj) * np.exp(-tj / 0.15)) * np.exp(-tj / 0.5)
    return s * 0.5
def boom():
    t = tt(1.6); f = 38 + 60 * np.exp(-t / 0.06); ph = 2 * np.pi * np.cumsum(f) / SR
    return np.sin(ph) * env(t, 0.003, 0.55) + lp(rng.standard_normal(len(t)), 900) * np.exp(-t / 0.12) * 0.5
def riser(d):
    t = tt(d); n = rng.standard_normal(len(t)); out = np.zeros_like(t); seg = 1024
    for i in range(0, len(t), seg):
        x = i / len(t); fc = 300 + 6000 * x ** 2
        out[i:i + seg] = bp(n[max(0, i - 4096):i + seg], fc * 0.8, min(fc * 1.3, 20000))[-len(n[i:i + seg]):]
    f = 220 + 600 * (t / d) ** 2; ph = 2 * np.pi * np.cumsum(f) / SR
    return (out * 0.8 + 0.25 * np.sin(ph)) * (t / d) ** 2

# ---------- music ----------
BEAT = 0.6
CHORDS = [[50, 57, 61, 64, 66], [47, 54, 57, 62, 66], [43, 50, 54, 59, 62], [45, 52, 57, 61, 64]]  # Dmaj9, Bm9-ish, Gmaj9, A6/9
ARP = [[74, 78, 81, 85, 81, 78, 76, 78], [71, 74, 78, 81, 78, 74, 73, 74], [67, 71, 74, 78, 74, 71, 69, 71], [69, 73, 76, 81, 76, 73, 71, 73]]
g0 = starts[plan[1][0]]; endS = starts['end']   # groove from the first scene after the hook
# hook: drone + riser + impact
hook_d = plan[0][1]; k = hook_d / 3.0
add(pad([38, 45, 50], hook_d * 0.95, att=0.4, rel=0.6), 0.0, 0.55, fx=0.4)
add(riser(1.25 * k), 0.12 * k, 0.22, fx=0.3)
add(whoosh(0.4, up=False), 0.78 * k, 0.25, pan=-0.3)          # the strike
add(boom(), 1.35 * k, 0.9)
add(pad(CHORDS[0], 1.2 * k, att=0.01, rel=0.8), 1.35 * k, 0.6, fx=0.6)
add(pop(600, 1500, 0.14), 1.62 * k, 0.35, fx=0.4)
# groove
bars_t = np.arange(g0, endS, BEAT)
for i, bt in enumerate(bars_t):
    ci = (i // 8) % 4
    if i % 8 == 0: add(pad(CHORDS[ci], 8 * BEAT, att=0.5, rel=1.2), bt, 0.42, fx=0.5)
    if i % 2 == 0: add(kick(), bt, 0.55)
    if i % 4 == 2 and bt > g0 + 4 * BEAT: add(snap(), bt, 0.16, fx=0.35)
    if bt >= starts['chat'] - 0.01: add(hat(), bt + BEAT / 2, 0.10, pan=0.4)
    for j in range(2):
        m = ARP[ci][(i % 4) * 2 + j]
        add(pluck(m), bt + j * BEAT / 2, 0.16, pan=(-0.35 if (i + j) % 2 else 0.35), fx=0.45)
# end card: resolve
add(boom(), endS + 0.02, 0.6)
add(pad([38, 50, 57, 61, 64, 66, 69], plan[-1][1] - 0.6, att=0.05, rel=1.5), endS + 0.02, 0.6, fx=0.8)
for j, m in enumerate([74, 78, 81, 85, 88]): add(pluck(m, 1.6), endS + 0.35 + j * 0.09, 0.14, pan=-0.4 + j * 0.2, fx=0.7)
add(chime((90, 97), 2.2), endS + 0.95, 0.22, fx=0.8)

# ---------- sound effects tied to the picture ----------
for n, d in plan[1:]:
    add(whoosh(0.6), starts[n] - 0.42, 0.20, pan=0.2, fx=0.2)
def S(scene, tau, sig, gain, pan=0.0, fx=0.25):
    if scene in starts: add(sig, starts[scene] + tau, gain, pan, fx)
# self-hosting: places pop in, the server drops and hops, lands, then the phone links up
for i in range(4): S('selfhost', 0.25 + i * 0.1, pop(520 + i * 110, 980 + i * 110, 0.09), 0.11, -0.45 + i * 0.3)
S('selfhost', 0.85, whoosh(0.35, up=False), 0.12, 0.3)
for j, a in enumerate((1.2, 1.68, 2.16, 2.64)):
    S('selfhost', a, kick(), 0.18 if j < 3 else 0.28, 0.25); S('selfhost', a, pop(900 + j * 140, 1400 + j * 140, 0.07), 0.12, 0.25)
S('selfhost', 2.66, chime((86, 93), 1.2), 0.18, 0.2, 0.5)
S('selfhost', 2.85, riser(0.4), 0.10, 0.4)
for i in range(5): S('selfhost', 3.2 + i * 0.08, pop(1000 + i * 90, 1500 + i * 90, 0.06), 0.08, -0.2 + i * 0.1)
# models: open the sheet, four picks, the local one gets a chime, sheet closes
S('models', 0.45, tick(), 0.25, 0.3); S('models', 0.55, whoosh(0.4), 0.12, 0.3)
for a in (1.35, 1.85, 2.35, 2.95): S('models', a, tick(2400), 0.24, 0.3); S('models', a + 0.28, pop(950, 1450, 0.08), 0.12, 0.3)
S('models', 3.3, chime((88, 95), 1.3), 0.2, -0.2, 0.5)
S('models', 4.02, tick(2000), 0.22, 0.3); S('models', 4.15, whoosh(0.4, up=False), 0.12, 0.3)
S('models', 4.55, pop(800, 1300), 0.14, 0.3)
# chat
for c in np.arange(0.75, 1.85, 0.055): S('chat', c + rng.uniform(-0.01, 0.01), key(), 0.12, 0.3, 0.05)
S('chat', 1.98, tick(), 0.25, 0.3); S('chat', 2.05, pop(700, 1200), 0.25, 0.3)
S('chat', 2.85, pop(900, 1400, 0.09), 0.12); S('chat', 3.35, pop(950, 1450, 0.09), 0.12)
S('chat', 3.95, chime((83, 90), 1.0), 0.18, 0.2)          # approval card
S('chat', 5.4, tick(2200), 0.32, 0.3); S('chat', 5.75, pop(800, 1300), 0.16)
S('chat', 6.45, chime((86, 93), 1.4), 0.22, 0.2, 0.5)        # done
# browser
S('browser', 0.2, pop(700, 1200), 0.2, -0.3); S('browser', 0.75, pop(850, 1300), 0.14, -0.3)
S('browser', 1.55, tick(3000), 0.26, 0.4); S('browser', 2.62, tick(3000), 0.26, 0.4); S('browser', 3.0, chime((86, 93), 1.2), 0.18, 0.0, 0.5)
# connectors
for i in range(6): S('connectors', 0.35 + i * 0.13 if fmt == 'h' else 0.3 + i * 0.1, pop(600 + i * 90, 1100 + i * 90, 0.08), 0.10, (-0.5 + i * 0.2))
S('connectors', 2.72, tick(), 0.28, 0.2); S('connectors', 3.07, pop(1000, 1600), 0.16, 0.2)
S('connectors', 3.42, tick(), 0.28, 0.2); S('connectors', 3.8, pop(1100, 1700), 0.16, 0.2)
# keychain
for c in np.arange(0.65, 1.6, 0.06): S('keychain', c, key(), 0.07, 0.4, 0.05)
S('keychain', 1.6, snap(), 0.22, 0.0, 0.3); S('keychain', 1.75, chime((81, 88), 0.9), 0.14)
# goals
S('goals', 1.5, pop(900, 1600, 0.1), 0.2); S('goals', 1.55, chime((88, 95), 0.8), 0.12)
S('goals', 2.6, whoosh(0.5), 0.16, -0.3)
# install (same speed-up as ad.js: shorter cuts run the terminal a little faster)
ik = max(1.0, 5.0 / dict(plan)['install']) if 'install' in starts else 1.0
for c in np.arange(0.35, 1.7, 0.03): S('install', (c + rng.uniform(-0.008, 0.008)) / ik, key(), 0.10, 0.2, 0.05)
S('install', 1.85 / ik, tick(1800), 0.28)
for a in (2.0, 2.3, 2.4, 2.65, 3.3, 3.5): S('install', a / ik, pop(1200, 1500, 0.05), 0.08, 0.3)
S('install', 3.9 / ik, chime((86, 93), 1.2), 0.22, 0.0, 0.5)

# ---------- mix ----------
ir_t = tt(1.8); ir = rng.standard_normal(len(ir_t)) * np.exp(-ir_t / 0.45); ir = lp(ir, 5000); ir /= np.sqrt(np.sum(ir ** 2))
revL = fftconvolve(FX_L, ir)[:N] * 0.55; revR = fftconvolve(FX_R, ir[::-1][::-1] * 1.0)[:N] * 0.55
L += revL; R += revR
mix = np.stack([L, R], 1)[: int(DUR * SR)]
fade = int(0.9 * SR); mix[-fade:] *= np.linspace(1, 0, fade)[:, None] ** 1.5
mix[: int(0.02 * SR)] *= np.linspace(0, 1, int(0.02 * SR))[:, None]
mix = np.tanh(mix * 0.9) / np.tanh(0.9)          # gentle glue
mix *= 0.89 / np.max(np.abs(mix))
import wave
w = wave.open(out, 'wb'); w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
w.writeframes((mix * 32767).astype('<i2').tobytes()); w.close()
print(out, DUR)

/* Kelo promo — every frame is a pure function of t (seconds). */
const Q = new URLSearchParams(location.search);
const LANG = Q.get('lang') || 'en', FMT = Q.get('fmt') || 'h', ONLY = Q.get('scene');
const GIF = FMT === 'gl' || FMT === 'gp';
document.body.classList.add(LANG, FMT);
const SIZES = { h: [1920, 1080], v: [1080, 1920], gl: [1600, 900], gp: [1200, 1500] };
const [W, H] = SIZES[FMT];
const stage = document.getElementById('stage');
stage.style.width = W + 'px'; stage.style.height = H + 'px';
document.body.style.width = W + 'px'; document.body.style.height = H + 'px';

const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const pr = (t, a, b) => clamp((t - a) / (b - a));
const eo = x => 1 - Math.pow(1 - x, 3);
const eo5 = x => 1 - Math.pow(1 - x, 5);
const eio = x => x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
const eback = x => { const c1 = 1.5, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
const lerp = (a, b, x) => a + (b - a) * x;
const el = html => { const d = document.createElement('div'); d.innerHTML = html.trim(); return d.firstElementChild; };
const $ = (r, s) => r.querySelector(s);
const $$ = (r, s) => [...r.querySelectorAll(s)];
const KO = LANG === 'ko';
const tr = (en, ko) => KO ? ko : en;

/* ---------------- icons ---------------- */
const sv = (p, w = 22, sw = 1.9, extra = '') => `<svg width="${w}" height="${w}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" ${extra}>${p}</svg>`;
const IC = {
  chat: w => sv('<path d="M12 4.2c4.5 0 8 3 8 6.9s-3.5 6.9-8 6.9c-1.2 0-2.3-.2-3.3-.6L5 19l1.1-3.4C4.8 14.3 4 12.8 4 11.1 4 7.2 7.5 4.2 12 4.2z"/>', w),
  goals: w => sv('<rect x="4" y="4" width="16" height="16" rx="3.2"/><path d="M8.4 12.3l2.5 2.5 4.8-5.1"/>', w),
  plug: w => sv('<path d="M9 3.5v4.5M15 3.5v4.5M6.5 8h11v3.2a5.5 5.5 0 0 1-11 0zM12 16.7V21"/>', w),
  me: w => sv('<circle cx="12" cy="8" r="3.8"/><path d="M4.6 20.2c.9-3.6 3.9-6 7.4-6s6.5 2.4 7.4 6"/>', w),
  plus: w => sv('<path d="M12 5v14M5 12h14"/>', w, 2),
  mic: w => sv('<rect x="9" y="3.5" width="6" height="11" rx="3"/><path d="M5.8 11.5a6.2 6.2 0 0 0 12.4 0M12 17.8v3"/>', w),
  up: w => sv('<path d="M12 19V5M6 11l6-6 6 6"/>', w, 2.2),
  back: w => sv('<path d="M19 12H5M11 6l-6 6 6 6"/>', w, 2),
  check: w => sv('<path d="M5 12.5l4.5 4.5L19 7.5"/>', w, 2.6),
  clock: w => sv('<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>', w),
  key: w => sv('<circle cx="8" cy="14" r="4"/><path d="M11 11l8-8M16 6l3 3M14 8l2 2"/>', w),
  spark: w => `<svg width="${w}" height="${w}" viewBox="0 0 24 24"><path d="M12 2.5c.6 4.6 2.9 6.9 7.5 7.5-4.6.6-6.9 2.9-7.5 7.5-.6-4.6-2.9-6.9-7.5-7.5 4.6-.6 6.9-2.9 7.5-7.5z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/></svg>`,
};
const WIFI = `<svg viewBox="0 0 20 16" width="19"><path d="M10 15.5L0.6 5.2C3.2 2.8 6.4 1.5 10 1.5s6.8 1.3 9.4 3.7z" fill="#fff"/></svg>`;
const BATT = `<svg viewBox="0 0 10 16" width="10"><rect x="0.5" y="2" width="9" height="13.5" rx="1.6" fill="#fff"/><rect x="3" y="0.3" width="4" height="2.2" rx=".6" fill="#fff"/></svg>`;
const CURSOR = `<svg class="cursor" viewBox="0 0 24 24"><path d="M4 2.5l15 11.3-6.6.9 3.9 7.2-3 1.6-3.9-7.3L4.6 21z" fill="#111" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>`;

/* ---------------- mascot ---------------- */
function mascot(cls = 'mwrap') { const d = document.createElement('div'); d.className = cls; return d; }
function setMascot(d, state, since = 9) {
  if (d.dataset.st !== state) { d.innerHTML = MASCOT[state]; d.dataset.st = state; }
  const k = clamp(since / 0.45);
  const s = since < 0.45 ? 1 + 0.16 * Math.sin(Math.PI * k) * (1 - k * 0.3) : 1;
  d.style.transform = `scale(${s})`;
}
function stateAt(list, t) { let cur = list[0]; for (const x of list) if (t >= x[0]) cur = x; return [cur[1], t - cur[0]]; }

/* ---------------- captions ---------------- */
function capEl(spec, o) {
  const c = document.createElement('div'); c.className = 'cap';
  c.style.cssText = `left:${o.x}px;top:${o.y}px;width:${o.w}px;text-align:${o.align};transform:translateY(-50%)`;
  const h = document.createElement('div'); h.className = 'head'; h.style.fontSize = o.fs + 'px';
  let acc = false;
  spec.head.split(' ').forEach((tok, i) => {
    if (tok === '|') { h.appendChild(document.createElement('br')); return; }
    if (i && h.lastChild && h.lastChild.nodeName !== 'BR') h.appendChild(document.createTextNode(' '));
    if (tok.startsWith('*')) acc = true;
    const w = document.createElement('span'); w.className = 'w';
    const wi = document.createElement('span'); wi.className = 'wi' + (acc ? ' acc' : '');
    wi.textContent = tok.replace(/\*/g, ''); w.appendChild(wi); h.appendChild(w);
    if (tok.replace(/[.,!?。]+$/, '').endsWith('*')) acc = false;
  });
  c.appendChild(h);
  if (spec.sub) { const s = document.createElement('div'); s.className = 'sub'; s.style.fontSize = o.sfs + 'px'; s.textContent = spec.sub; c.appendChild(s); }
  c._a = spec.a; c._b = spec.b ?? 999;
  return c;
}
function updCap(c, t) {
  const a = c._a, b = c._b;
  if (t < a - 0.01 || t > b + 0.05) { c.style.display = 'none'; return; }
  c.style.display = '';
  $$(c, '.wi').forEach((wi, i) => {
    const p = eo5(pr(t, a + i * 0.055, a + i * 0.055 + 0.55));
    const q = eio(pr(t, b - 0.34 + i * 0.02, b - 0.06 + i * 0.02));
    wi.style.transform = `translateY(${(1 - p) * 110 - q * 110}%)`;
  });
  const s = $(c, '.sub');
  if (s) { const p = eo(pr(t, a + 0.3, a + 0.8)), q = pr(t, b - 0.32, b - 0.08); s.style.opacity = p * (1 - q); s.style.transform = `translateY(${(1 - p) * 22 - q * 10}px)`; }
}

/* ---------------- layout ---------------- */
const L = {
  h: { cap: { x: 160, y: 540, w: 830, fs: 82, sfs: 30, align: 'left' }, ph: { cx: 1350, cy: 534, s: 1.2 } },
  v: { cap: { x: 60, y: 330, w: 960, fs: 82, sfs: 36, align: 'center' }, ph: { cx: 540, cy: 1307, s: 1.42 } },
  gp: { cap: { x: 50, y: 205, w: 1100, fs: 68, sfs: 31, align: 'center' }, ph: { cx: 600, cy: 994, s: 1.05 } },
  gl: { cap: { x: 100, y: 140, w: 1400, fs: 62, sfs: 28, align: 'center' }, ph: { cx: 800, cy: 450, s: 0.8 } },
}[FMT];

/* ---------------- phone + app chrome ---------------- */
function phoneEl(inner) { return el(`<div class="phone"><div class="scr"><div class="cam"></div><div class="app">${inner}</div></div></div>`); }
function placePhone(ph, cx, cy, s, rot = '') { ph.style.transform = `translate(${cx - 218}px,${cy - 470}px) perspective(2200px) ${rot} scale(${s})`; }
const SBAR = `<div class="sbar"><span>9:41</span><span style="display:flex;align-items:center">${WIFI}${BATT}</span></div>`;
function NAV(on) {
  const tabs = [[IC.chat, tr('Chat', '채팅')], [IC.goals, tr('Goals', '다짐')], [IC.plug, tr('Connectors', '커넥터')], [IC.me, tr('My', '마이')]];
  return `<div class="nav">${tabs.map((x, i) => `<div class="tb ${i === on ? 'on' : ''}"><div class="pill">${x[0](21)}</div>${x[1]}</div>`).join('')}</div><div class="gbar"></div>`;
}
const HDR = `<div class="hdr"><div class="av"></div><div class="namechip">Kelo</div></div>`;
const MODEL0 = tr('Sonnet 5.5 Medium', 'Sonnet 5.5 보통');
const CHEV = `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9.5l6 6 6-6"/></svg>`;
const COMP = `<div class="comp"><div class="ph"></div><span class="ic" style="left:14px">${IC.plus(24)}</span><span class="mdl"><span class="mdlt">${MODEL0}</span>${CHEV}</span><span class="ic" style="right:58px">${IC.mic(22)}</span><div class="sendb">${IC.up(20)}</div></div>`;
const TAP = `<div class="ripple"></div><div class="tap"></div>`;
function offsetIn(e, anc) { let x = 0, y = 0; while (e && e !== anc) { x += e.offsetLeft; y += e.offsetTop; e = e.offsetParent; } return [x, y]; }
function centerIn(e, anc) { const [x, y] = offsetIn(e, anc); return [x + e.offsetWidth / 2, y + e.offsetHeight / 2]; }
// finger: path = [[t,x,y],...], press = time of press
function updTap(app, t, path, press) {
  const tap = $(app, '.tap'), rip = $(app, '.ripple');
  const t0 = path[0][0], t1 = press + 0.42;
  if (t < t0 - 0.2 || t > t1) { tap.style.display = rip.style.display = 'none'; return; }
  tap.style.display = '';
  let x = path[0][1], y = path[0][2];
  for (let i = 1; i < path.length; i++) { const [ta, xa, ya] = path[i - 1], [tb, xb, yb] = path[i]; if (t >= ta) { const k = eio(pr(t, ta, tb)); x = lerp(xa, xb, k); y = lerp(ya, yb, k); } }
  const op = pr(t, t0 - 0.2, t0) * (1 - pr(t, press + 0.22, t1));
  const sc = 1 - 0.2 * pr(t, press - 0.06, press + 0.04) + 0.2 * pr(t, press + 0.08, press + 0.28);
  tap.style.cssText = `left:${x}px;top:${y}px;opacity:${op};transform:scale(${sc})`;
  const rp = pr(t, press, press + 0.45);
  rip.style.cssText = `display:${t >= press ? '' : 'none'};left:${x}px;top:${y}px;opacity:${0.9 * (1 - rp)};transform:scale(${1 + rp * 1.6})`;
}

/* chat items: each item = {at, node, kind}; heights grow so older bubbles get pushed up */
function addItem(msgs, html, at, side = 'l') {
  const it = el(`<div class="it"><div class="iti">${html}</div></div>`);
  msgs.appendChild(it); return { at, it, inner: it.firstElementChild, side };
}
function updItem(x, t) {
  if (t < x.at) { x.it.style.display = 'none'; return; }
  x.it.style.display = '';
  const p = eo(pr(t, x.at, x.at + 0.42));
  if (x.custom) { x.custom(t, p); return; }
  x.it.style.height = p < 1 ? (x.inner.offsetHeight * eo5(pr(t, x.at, x.at + 0.32))) + 'px' : '';
  x.inner.style.opacity = clamp(p * 1.4);
  x.inner.style.transformOrigin = x.side === 'r' ? '100% 100%' : '0% 100%';
  x.inner.style.transform = `translateY(${(1 - p) * 16}px) scale(${0.94 + 0.06 * p})`;
}
const md = s => s.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>');
function chipHTML(text) { return `<span class="chip"><span class="spin"></span><span class="ck" style="display:none">✓</span><span>${text}</span></span>`; }
function updChip(x, t, done) {
  const spin = $(x.inner, '.spin'), ck = $(x.inner, '.ck');
  const d = t >= done; spin.style.display = d ? 'none' : ''; ck.style.display = d ? '' : 'none';
  spin.style.transform = `rotate(${t * 540}deg)`;
}
function updStatus(app, t, a, b, list) {
  const s = $(app, '.stl');
  const op = pr(t, a, a + 0.25) * (1 - pr(t, b - 0.05, b + 0.2));
  s.style.opacity = op; s.style.display = op > 0 ? '' : 'none';
  if (op <= 0) return;
  const [txt] = stateAt(list, t);
  $(s, '.stt').textContent = `${txt}   ${Math.max(1, Math.floor(t - a) + 1)}s`;
  $$(s, '.dots span').forEach((d, i) => { d.style.opacity = 0.35 + 0.65 * Math.max(0, Math.sin(t * 6 - i * 0.9)); });
}
function updSend(app, mode) {
  const b = $(app, '.sendb');
  if (mode === 'stop') { b.style.background = '#f2f2f5'; b.innerHTML = '<div style="width:12px;height:12px;background:#111;border-radius:2px"></div>'; }
  else { b.style.background = mode === 'ready' ? 'var(--lav)' : '#3a3a42'; b.style.color = mode === 'ready' ? '#11122e' : '#8b8b94'; if (!b.querySelector('svg')) b.innerHTML = IC.up(20); }
}

/* ================= scenes ================= */
const SC = {};

/* ---- hook ---- */
SC.hook = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const k = dur / 3.0;
  const fs = { h: 112, v: 104, gl: 90, gp: 80 }[FMT];
  const l1 = el(`<div class="abs" style="left:0;right:0;text-align:center;font-weight:800;letter-spacing:-0.03em;font-size:${fs}px;color:#8c8c9c"><span class="t1" style="position:relative;display:inline-block">${tr('Most AI just talks.', '대답만 하는 AI 말고,')}<span class="strike" style="position:absolute;left:-2%;top:54%;height:${fs * 0.075}px;background:#ff6b7a;border-radius:9px;width:0"></span></span></div>`);
  const l2 = el(`<div class="abs" style="left:0;right:0;text-align:center;font-weight:850;letter-spacing:-0.035em;font-size:${fs * 1.08}px"></div>`);
  const words = KO ? ['*직접', '해내는*', 'AI,', 'Kelo.'] : ['Kelo', 'gets', 'it', '*done.*'];
  let acc = false;
  words.forEach((w, i) => { if (i) l2.appendChild(document.createTextNode(' ')); if (w.startsWith('*')) acc = true; l2.appendChild(el(`<span class="w" style="display:inline-block;overflow:hidden;vertical-align:top;padding:.05em .02em .12em;margin:-.05em -.02em -.12em"><span class="wi ${acc ? 'acc' : ''}" style="display:inline-block">${w.replace(/\*/g, '')}</span></span>`)); if (w.endsWith('*') || w.endsWith('*.')) acc = false; });
  const m = mascot('hm'); m.style.cssText = 'position:absolute;';
  root.append(l1, l2, m);
  const msz = { h: 250, v: 300, gl: 210, gp: 230 }[FMT];
  const cy = H / 2 + ({ h: 70, v: 90, gl: 60, gp: 60 }[FMT]);
  return {
    root, update(t) {
      const T = t / k;
      const p1 = eo5(pr(T, 0.05, 0.6)), q1 = eio(pr(T, 1.25, 1.6));
      l1.style.top = (cy - fs * 0.62) + 'px';
      l1.style.opacity = p1 * (1 - q1);
      l1.style.transform = `translateY(${(1 - p1) * 50 - q1 * 90}px) scale(${1 - q1 * 0.06})`;
      l1.style.filter = `blur(${(1 - p1) * 12 + q1 * 8}px)`;
      $(l1, '.strike').style.width = (104 * eio(pr(T, 0.8, 1.15))) + '%';
      l2.style.top = (cy - fs * 0.6) + 'px';
      $$(l2, '.wi').forEach((wi, i) => { const p = eo5(pr(T, 1.35 + i * 0.07, 1.95 + i * 0.07)); wi.style.transform = `translateY(${(1 - p) * 110}%)`; });
      const mp = pr(T, 1.55, 2.15);
      m.style.width = m.style.height = msz + 'px';
      m.style.left = (W / 2 - msz / 2) + 'px'; m.style.top = (cy - fs * 0.6 - msz - 10) + 'px';
      m.style.opacity = clamp(mp * 3);
      setMascot(m, 'done', 9);
      m.style.transform = `translateY(${(1 - eback(mp)) * 160}px) scale(${0.5 + 0.5 * eback(mp)})`;
    }
  };
};

/* ---- self-hosting: the Kelo server goes where you want it ---- */
const PLACE_IC = {
  cloud: w => sv('<path d="M7 18h10.6a4.1 4.1 0 0 0 .5-8.2A5.6 5.6 0 0 0 7.3 11 3.6 3.6 0 0 0 7 18z"/><path d="M10 14.6h4.4"/>', w, 1.6),
  linux: w => sv('<rect x="4" y="4" width="16" height="7" rx="1.8"/><rect x="4" y="13" width="16" height="7" rx="1.8"/><path d="M7.4 7.5h.01M7.4 16.5h.01M11 7.5h5.5M11 16.5h5.5"/>', w, 1.6),
  nas: w => sv('<rect x="5.5" y="3.5" width="13" height="17" rx="2.2"/><path d="M8.5 7.5h7M8.5 11h7M8.5 14.5h7"/><path d="M12 17.7h.01"/>', w, 1.6),
  home: w => sv('<path d="M3.5 11.2L12 4.2l8.5 7"/><path d="M5.6 9.6V20h12.8V9.6"/><rect x="8.6" y="11.6" width="6.8" height="4.6" rx="1"/><path d="M12 16.2v1.6M10.4 17.9h3.2"/>', w, 1.6),
};
const LOCK = w => sv('<rect x="5" y="10.5" width="14" height="10" rx="2.4"/><path d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><path d="M12 14.5v2.2"/>', w, 1.9);
const SH = {
  // grid order: top-left, top-right, bottom-left, bottom-right. The server hops in that order and stays on the last one.
  places: [['cloud', tr('Cloud server', '클라우드 서버')], ['linux', tr('Linux server', 'Linux 서버')], ['nas', tr('Mini PC · NAS', '미니 PC · NAS')], ['home', tr('Mac at home', '집에 있는 Mac')]],
  badge: tr('Kelo server', 'Kelo 서버'),
  keep: KO ? ['대화', '기억', '파일', '키'] : ['Chats', 'Memory', 'Files', 'Keys'],
  keepT: tr('stay on your server', '모두 Kelo 서버에'),
  cap: { a: 0.15, head: tr('Your server. | *Where you want it.*', 'Kelo 서버는 | *원하는 곳에.*'), sub: tr('A Mac at home, a Linux box, a NAS or the cloud. Your phone is the remote.', '집에 있는 Mac, Linux 서버, NAS, 클라우드 서버 어디든 좋아요. 휴대폰은 리모컨이에요.') },
};
SC.selfhost = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const C = {
    h: { gx: 985, gy: 272, cw: 296, ch: 200, gap: 24, f: 1.08, ph: [1712, 452, 150, 300], keepY: 790, keepX: 1412, cap: L.cap },
    v: { gx: 75, gy: 600, cw: 450, ch: 262, gap: 30, f: 1.45, ph: [445, 1262, 190, 386], keepY: 1745, keepX: 540, cap: L.cap },
    gl: { gx: 380, gy: 280, cw: 270, ch: 172, gap: 24, f: 0.95, ph: [1080, 428, 140, 284], keepY: 752, keepX: 800, cap: Object.assign({}, L.cap, { y: 128 }) },
    gp: { gx: 90, gy: 460, cw: 495, ch: 290, gap: 30, f: 1.5, ph: [505, 1095, 190, 300], keepY: 1440, keepX: 600, cap: L.cap },
  }[FMT];
  const f = C.f;
  const pos = i => [C.gx + (i % 2) * (C.cw + C.gap), C.gy + Math.floor(i / 2) * (C.ch + C.gap)];
  const cards = SH.places.map(([ic, name], i) => {
    const [x, y] = pos(i);
    const d = el(`<div class="place" style="left:${x}px;top:${y}px;width:${C.cw}px;height:${C.ch}px"><div class="pic" style="left:${22 * f}px;top:${20 * f}px">${PLACE_IC[ic](58 * f)}</div><div class="pt" style="left:${24 * f}px;bottom:${22 * f}px;font-size:${25 * f}px;width:${C.cw - 48 * f}px">${name}</div></div>`);
    root.appendChild(d); return d;
  });
  // link phone <-> server
  const lk = el(`<svg class="abs" style="left:0;top:0;width:${W}px;height:${H}px;overflow:visible" viewBox="0 0 ${W} ${H}"><line stroke="#a9acff" stroke-width="${3 * f}" stroke-linecap="round" stroke-dasharray="${2 * f} ${11 * f}"/>${[0, 1, 2].map(() => `<circle r="${6 * f}" fill="#a9acff"/>`).join('')}</svg>`);
  root.appendChild(lk);
  const [px, py, pw, phh] = C.ph;
  const mp = el(`<div class="mphone" style="left:${px}px;top:${py}px;width:${pw}px;height:${phh}px"><div class="ms">
    <div class="abs" style="left:50%;top:${pw * 0.13}px;transform:translateX(-50%);display:flex;flex-direction:column;align-items:center">
      <div class="mav" style="width:${pw * 0.3}px;height:${pw * 0.3}px;border-radius:50%;background:#fff3d6;display:flex;align-items:center;justify-content:center"></div>
      <div style="margin-top:-4px;position:relative;background:#1f1f23;border:1px solid #34343a;border-radius:8px;padding:0 ${pw * 0.05}px;font-size:${pw * 0.075}px;font-weight:700;color:#fff">Kelo</div></div>
    <div class="abs" style="left:${pw * 0.08}px;top:${phh * 0.42}px;width:${pw * 0.56}px;height:${pw * 0.1}px;border-radius:${pw * 0.05}px;background:#26262b"></div>
    <div class="abs" style="right:${pw * 0.08}px;top:${phh * 0.52}px;width:${pw * 0.46}px;height:${pw * 0.1}px;border-radius:${pw * 0.05}px;background:#343a6b"></div>
    <div class="abs" style="left:${pw * 0.08}px;top:${phh * 0.62}px;width:${pw * 0.62}px;height:${pw * 0.1}px;border-radius:${pw * 0.05}px;background:#26262b"></div>
    <div class="abs" style="left:${pw * 0.06}px;right:${pw * 0.06}px;bottom:${phh * 0.06}px;height:${pw * 0.16}px;border-radius:${pw * 0.06}px;background:#1f1f23;border:1px solid #34343a"></div>
  </div></div>`);
  root.appendChild(mp);
  const mm = mascot(); mm.style.cssText = `width:${pw * 0.27}px;height:${pw * 0.27}px`; $(mp, '.mav').appendChild(mm);
  const keep = el(`<div class="keep" style="left:0;top:${C.keepY}px;gap:${10 * f}px;font-size:${21 * f}px"><span style="color:#7fdc9b;display:flex">${LOCK(28 * f)}</span>${SH.keep.map(k => `<span class="kp" style="padding:${4 * f}px ${15 * f}px ${5 * f}px">${k}</span>`).join('')}<span class="kt" style="margin-left:${4 * f}px">${SH.keepT}</span></div>`);
  root.appendChild(keep);
  const kb = el(`<div class="kbadge"><div class="kav" style="width:${78 * f}px;height:${78 * f}px"></div><div class="kchip" style="font-size:${15 * f}px;padding:${1 * f}px ${11 * f}px ${2 * f}px">${SH.badge}</div></div>`);
  root.appendChild(kb);
  const km = mascot(); km.style.cssText = `width:${70 * f}px;height:${70 * f}px;margin-top:${3 * f}px`; $(kb, '.kav').appendChild(km);
  const cap = capEl(SH.cap, C.cap); root.appendChild(cap);
  // where the badge sits on card i (centre of its round avatar)
  const spot = i => { const [x, y] = pos(i); return [x + C.cw - 74 * f, y + 56 * f]; };
  const LAND = [1.2, 1.68, 2.16, 2.64];
  const fin = 3;
  return {
    root, update(t) {
      cards.forEach((c, i) => {
        const a = 0.25 + i * 0.1, p = eback(pr(t, a, a + 0.5));
        c.style.opacity = clamp(pr(t, a, a + 0.2));
        c.style.transform = `translateY(${(1 - p) * 50}px) scale(${0.86 + 0.14 * p})`;
        // lit while the server sits on it
        let on = 0;
        for (let j = 0; j < 4; j++) if (j === i) { const st = LAND[j], en = j === fin ? 99 : LAND[j + 1] - 0.36; on = Math.max(on, pr(t, st - 0.05, st + 0.1) * (1 - pr(t, en, en + 0.15))); }
        const glow = i === fin ? on : on * 0.8;
        c.style.borderColor = `rgba(${lerp(52, 169, glow)},${lerp(52, 172, glow)},${lerp(63, 255, glow)},${0.4 + 0.6 * glow})`;
        c.style.boxShadow = `0 24px 60px rgba(0,0,0,.45), 0 0 ${40 * glow * f}px rgba(140,143,247,${0.35 * glow})`;
        c.style.background = glow ? `rgba(${lerp(23, 32, glow)},${lerp(23, 32, glow)},${lerp(31, 52, glow)},1)` : '';
      });
      // badge: drop onto the first card, then hop across the grid
      let bx, by, sq = 0;
      const [x0, y0] = spot(0);
      if (t < LAND[0]) { const k = eo(pr(t, 0.85, LAND[0])); bx = x0; by = lerp(y0 - 260 * f, y0, k * k); }
      else {
        bx = x0; by = y0;
        for (let j = 1; j < 4; j++) {
          const a = LAND[j] - 0.36, b = LAND[j];
          if (t >= a) { const [xa, ya] = spot(j - 1), [xb, yb] = spot(j); const k = eio(pr(t, a, b)); bx = lerp(xa, xb, k); by = lerp(ya, yb, k) - Math.sin(Math.PI * k) * 70 * f; }
        }
      }
      for (const L0 of LAND) { const d = t - L0; if (d >= 0 && d < 0.22) sq = Math.sin(Math.PI * d / 0.22); }
      kb.style.left = (bx - 39 * f) + 'px'; kb.style.top = (by - 39 * f) + 'px';
      kb.style.opacity = clamp(pr(t, 0.85, 0.98));
      kb.style.transformOrigin = '50% 100%';
      kb.style.transform = `scale(${1 + 0.1 * sq}, ${1 - 0.12 * sq})`;
      const [st, since] = stateAt([[-9, 'idle'], [LAND[3], 'done']], t); setMascot(km, st, since);
      // phone and the link to the chosen server
      const pe = eo5(pr(t, 2.45, 3.0));
      mp.style.opacity = clamp(pr(t, 2.45, 2.7));
      mp.style.transform = FMT === 'v' ? `translateY(${(1 - pe) * 120}px)` : `translateX(${(1 - pe) * 120}px)`;
      setMascot(mm, t < 3.25 ? 'idle' : 'done', t < 3.25 ? 9 : t - 3.25);
      const [sx, sy] = (() => { const [x, y] = pos(fin); return FMT === 'v' || FMT === 'gp' ? [x + C.cw / 2, y + C.ch] : [x + C.cw, y + C.ch / 2]; })();
      const [ex, ey] = FMT === 'v' || FMT === 'gp' ? [px + pw / 2, py] : [px, py + phh / 2];
      const ln = $(lk, 'line'), dk = eio(pr(t, 2.85, 3.25));
      ln.setAttribute('x1', sx); ln.setAttribute('y1', sy); ln.setAttribute('x2', lerp(sx, ex, dk)); ln.setAttribute('y2', lerp(sy, ey, dk));
      ln.style.opacity = dk > 0 ? 1 : 0;
      $$(lk, 'circle').forEach((c, i) => {
        const ph2 = ((t - 3.2) * 0.9 + i / 3) % 1, on = t > 3.2 ? 1 : 0;
        const k = i === 1 ? 1 - ph2 : ph2; // one goes back, two go out
        c.setAttribute('cx', lerp(ex, sx, k)); c.setAttribute('cy', lerp(ey, ey + (sy - ey), k));
        c.style.opacity = on * Math.sin(Math.PI * ph2) * clamp(pr(t, 3.2, 3.4));
      });
      // what stays on the server
      keep.style.width = W + 'px';
      keep.style.left = (C.keepX - W / 2) + 'px';
      $$(keep, ':scope > span').forEach((s, i) => { const a = 3.2 + i * 0.08, p = eo5(pr(t, a, a + 0.45)); s.style.opacity = p; s.style.transform = `translateY(${(1 - p) * 18}px)`; });
      updCap(cap, t);
    }
  };
};

/* ---- models: any provider, or a local model ---- */
const MD = {
  rows: [['Anthropic', 'Claude Sonnet 5.5', 'Sonnet 5.5', tr('Complex tasks · Understands photos', '복잡한 일 · 사진 이해')],
  ['OpenAI', 'GPT-5', 'GPT-5', tr('Step-by-step reasoning · Understands photos', '단계별 추론 · 사진 이해')],
  ['Google', 'Gemini 3 Pro', 'Gemini 3 Pro', tr('Understands photos · Long chats', '사진 이해 · 긴 대화')],
  ['DeepSeek', 'DeepSeek V3.2', 'DeepSeek V3.2', tr('Step-by-step reasoning', '단계별 추론')],
  ['LM Studio', 'Qwen3 8B', 'Qwen3 8B', tr('Text chat', '글로 대화')]],
  title: tr('Model', '모델'), cur: tr('Model', '모델'),
  more: tr('More settings', '자세히 설정'), moreH: tr('Fallback model, models not on the list', '대체 모델, 목록에 없는 모델 이름'),
  eff: tr('Medium', '보통'),
  u: tr("What's on my calendar tomorrow?", '내일 일정 뭐 있어?'),
  b: tr('Two things: **10:00** team sync and **19:00** dinner with Mina.', '두 개 있어요. **오전 10시** 팀 회의, **저녁 7시** 민아 님과 저녁 약속이에요.'),
  badgeT: tr('Local model', '로컬 모델'), badgeS: tr('Stays on your own hardware', '대화가 밖으로 나가지 않아요'),
  caps: [
    { a: 0.2, b: 3.0, head: tr('Pick *any model.*', '모델은 | *원하는 걸로.*'), sub: tr('Claude, GPT, Gemini, DeepSeek, Qwen, Mistral, Grok, OpenRouter — about 30 providers.', 'Claude, GPT, Gemini, DeepSeek, Qwen, Mistral, Grok, OpenRouter 등 약 30곳에서 골라요.') },
    { a: 3.0, head: tr('Even a *local one.*', '*로컬 모델*도 돼요.'), sub: tr('LM Studio, Ollama or any OpenAI-compatible server. Your chats never leave your hardware.', 'LM Studio, Ollama 같은 OpenAI 호환 서버로 직접 돌려요. 대화가 밖으로 나가지 않아요.') },
  ],
};
SC.models = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const SPIN = `<span class="spin" style="width:16px;height:16px"></span>`;
  const sheet = `<div class="scrim"></div><div class="sheet"><div class="hnd"></div><div class="sht"><span>${MD.title}</span><span>${sv('<path d="M6 6l12 12M18 6L6 18"/>', 22, 2)}</span></div>
    <div class="cur"><div><div class="l1">${MD.cur}</div><div class="l2 cprov"></div></div><div class="cv"><span class="cmod"></span>${CHEV}</div></div>
    ${MD.rows.map(r => `<div class="sh">${r[0]}</div><div class="mr"><div><div class="n">${r[1]}</div><div class="b">${r[3]}</div></div><span class="ck2">${IC.check(20)}</span><span class="sp2">${SPIN}</span></div>`).join('')}
    <div class="more"><div class="l1">${MD.more}</div><div class="l2">${MD.moreH}</div></div></div>`;
  const ph = phoneEl(SBAR + HDR + `<div class="msgs"></div>` + COMP + NAV(0) + sheet + TAP);
  root.appendChild(ph);
  const app = $(ph, '.app'), msgs = $(app, '.msgs');
  const m = mascot(); $(app, '.av').appendChild(m);
  const it = [addItem(msgs, `<div class="bb">${CHAT.greet}</div>`, -1), addItem(msgs, `<div class="ub">${MD.u}</div>`, -1, 'r'), addItem(msgs, `<div class="bb">${md(MD.b)}</div>`, -1)];
  $(app, '.comp .ph').textContent = tr('Send a message', '메시지 보내기');
  const shEl = $(app, '.sheet'), scrim = $(app, '.scrim'), mrs = $$(app, '.mr'), mdl = $(app, '.comp .mdl');
  const V = FMT === 'v';
  const bf = { h: 1, v: 1.25, gp: 1.0, gl: 0.9 }[FMT];
  const lb = el(`<div class="lbadge" style="gap:${14 * bf}px;padding:${14 * bf}px ${22 * bf}px ${14 * bf}px ${16 * bf}px;${V ? '' : `width:${{ h: 330, gp: 300, gl: 300 }[FMT]}px`}"><span style="color:#6ee7a8;display:flex;flex:none">${LOCK(34 * bf)}</span><div style="${V ? 'display:flex;align-items:baseline;gap:12px;white-space:nowrap' : ''}"><div class="lt" style="font-size:${23 * bf}px">${MD.badgeT}</div><div class="ls" style="font-size:${18 * bf}px;line-height:1.3">${V ? '· ' : ''}${MD.badgeS}</div></div></div>`);
  root.appendChild(lb);
  const caps = MD.caps.map(c => capEl(c, L.cap)); root.append(...caps);
  // selection: tap times per row (row 0 is selected when the sheet opens)
  const TAPS = [-9, 1.35, 1.85, 2.35, 2.95];
  const OPEN = 0.55, CLOSE = 4.15;
  return {
    root, update(t) {
      const P = L.ph; const e = GIF || V ? 1 : eo5(pr(t, -0.15, 0.85));
      const dx = FMT === 'h' ? (1 - e) * 700 : 0;
      const ry = FMT === 'h' ? lerp(-28, -7, e) + Math.sin(t * 0.7) * 1.5 : Math.sin(t * 0.6) * 2;
      placePhone(ph, P.cx + dx, P.cy, P.s, `rotateY(${ry}deg) rotateX(${FMT === 'h' ? 2 : 1}deg)`);
      it.forEach(x => updItem(x, t));
      // which row is selected; a short spinner between tap and check
      let sel = 0, busy = -1;
      TAPS.forEach((tt2, i) => { if (t >= tt2 + 0.28) sel = i; else if (t >= tt2 + 0.02) busy = i; });
      mrs.forEach((r, i) => {
        $(r, '.ck2').style.display = i === sel ? '' : 'none';
        $(r, '.sp2').style.display = i === busy ? '' : 'none';
        $(r, '.sp2 .spin').style.transform = `rotate(${t * 540}deg)`;
        $(r, '.n').style.fontWeight = i === sel ? '700' : '400';
        const hl = i === busy ? 1 : 0; r.style.background = hl ? 'rgba(255,255,255,.06)' : '';
      });
      const R = MD.rows[sel];
      $(app, '.cprov').textContent = R[0]; $(app, '.cmod').textContent = R[1];
      $(mdl, '.mdlt').textContent = sel === 4 ? R[2] : sel === 0 ? MODEL0 : R[2] + ' ' + MD.eff;
      // sheet in / out
      const so = eo5(pr(t, OPEN, OPEN + 0.45)) * (1 - eio(pr(t, CLOSE, CLOSE + 0.4)));
      shEl.style.transform = `translateY(${(1 - so) * 640}px)`; shEl.style.visibility = so > 0.001 ? '' : 'hidden';
      scrim.style.opacity = so; scrim.style.visibility = so > 0.001 ? '' : 'hidden';
      const gl2 = pr(t, CLOSE + 0.35, CLOSE + 0.6) * (1 - pr(t, CLOSE + 1.3, CLOSE + 1.7));
      mdl.style.background = `rgba(140,143,247,${0.28 * gl2})`; mdl.style.boxShadow = `0 0 0 ${2 * gl2}px rgba(169,172,255,${0.6 * gl2})`;
      // finger: model button, the four rows, then close
      const [mx, my] = centerIn(mdl, app), [cxx, cyy] = centerIn($(app, '.sht span:last-child'), app);
      const rowC = i => { const [x, y] = centerIn(mrs[i], app); return [x + 40, y]; };
      if (t < 1.0) updTap(app, t, [[0.05, mx + 30, my + 60], [0.4, mx, my]], 0.45);
      else if (t < 3.6) {
        const path = [[1.0, ...rowC(0)]]; for (let i = 1; i < 5; i++) path.push([TAPS[i] - 0.05, ...rowC(i)]);
        const k = [1, 2, 3, 4].reduce((a, i) => t >= TAPS[i] - 0.3 ? i : a, 1);
        updTap(app, t, path, TAPS[k]);
      } else updTap(app, t, [[3.65, ...rowC(4)], [3.98, cxx, cyy]], 4.02);
      const [st, since] = stateAt([[-9, 'idle'], [TAPS[4] + 0.28, 'done']], t); setMascot(m, st, since);
      // local badge, outside the phone
      const be = eback(pr(t, 3.3, 3.85));
      lb.style.opacity = clamp(pr(t, 3.3, 3.5));
      if (V) { lb.style.left = '50%'; lb.style.top = '560px'; lb.style.transform = `translate(-50%,-50%) translateY(${(1 - be) * 40}px) scale(${0.8 + 0.2 * be})`; }
      else {
        const [, ry4] = rowC(4); const sy = P.cy + (ry4 + 12 - 470) * P.s;
        // h: left of the phone (under the caption); GIFs: right of the phone
        const left = FMT === 'h';
        const sx = left ? P.cx - 218 * P.s - 34 - 330 : P.cx + 218 * P.s + ({ gp: 22, gl: 30 }[FMT]);
        lb.style.left = sx + 'px'; lb.style.top = sy + 'px';
        lb.style.transform = `translateY(-50%) translateX(${(1 - be) * (left ? 40 : -40)}px) scale(${0.8 + 0.2 * be})`;
      }
      caps.forEach(c => updCap(c, t));
    }
  };
};

/* ---- chat (task + approval) ---- */
const CHAT = {
  greet: tr('Hi Alex! What can I take off your plate today?', '안녕하세요, Alex 님! 오늘은 뭘 도와드릴까요?'),
  user: tr('Plan 3 workouts a week and put them on my calendar.', '주 3회 운동 계획 짜서 캘린더에 넣어 줘'),
  c1: tr('Checking your calendar', '캘린더 확인'), c2: tr('Building your routine', '운동 루틴 만들기'),
  apT: tr('Allow adding events?', '일정 추가를 허용할까요?'),
  apB: tr('Add to Google Calendar: Upper body · Lower body · Full body · Mon, Wed, Fri · 19:00–20:00', 'Google 캘린더에 추가: 상체 · 하체 · 전신 운동 · 월·수·금 · 19:00–20:00'),
  deny: tr('Deny', '거절'), allow: tr('Allow', '허용'), allowed: tr('Allowed', '허용함'),
  c3: tr('Added 3 events to Google Calendar', 'Google 캘린더에 일정 3개 추가'),
  bot: tr("Done! Your workouts are on your calendar — **Mon, Wed and Fri at 7 PM**. I'll check in with you on Sunday.", '다 됐어요! **월·수·금 저녁 7시**에 운동 일정을 넣었어요. 일요일에 같이 돌아볼게요.'),
  st: [[0, tr('Thinking', '생각 중')], [0.8, tr('Checking your calendar', '캘린더 확인 중')], [1.3, tr('Building your routine', '루틴 만드는 중')], [1.9, tr('Waiting for approval · Adding events', '승인 대기 중 · 일정 추가')], [3.6, tr('Adding events', '일정 추가 중')]],
  caps: [
    { a: 0.25, b: 3.85, head: tr('Just *ask.*', '그냥 *말하세요.*'), sub: tr('Plain words. Kelo plans the steps.', '평소 말투 그대로요. 순서는 Kelo가 짜요.') },
    { a: 3.85, b: 5.95, head: tr('It asks you *first.*', '실행 전엔 *꼭 물어봐요.*'), sub: tr('Nothing important happens without your OK.', '중요한 일은 허락을 받고 해요.') },
    { a: 5.95, head: tr('Then it gets it *done.*', '그리고 *끝까지 해내요.*'), sub: tr('Real work, handled by your Kelo server.', '일은 Kelo 서버가 처리해요.') },
  ],
};
SC.chat = (dur, prev) => {
  const still = GIF || prev === 'models'; // the phone is already on screen after the model scene
  const root = el(`<div class="scene"></div>`);
  const ph = phoneEl(SBAR + HDR + `<div class="msgs"></div><div class="stl"><span class="dots"><span></span><span></span><span></span></span><span class="stt"></span></div>` + COMP + NAV(0) + TAP);
  root.appendChild(ph);
  const app = $(ph, '.app'), msgs = $(app, '.msgs');
  const m = mascot(); $(app, '.av').appendChild(m);
  const it = [];
  it.push(addItem(msgs, `<div class="bb">${CHAT.greet}</div>`, -1));
  const iu = addItem(msgs, `<div class="ub">${CHAT.user}</div>`, 2.05, 'r'); it.push(iu);
  const ic1 = addItem(msgs, chipHTML(CHAT.c1), 2.85); it.push(ic1);
  const ic2 = addItem(msgs, chipHTML(CHAT.c2), 3.35); it.push(ic2);
  const iap = addItem(msgs, `<div style="position:relative"><div class="acard" style="position:absolute;left:0;right:0;top:0"><h4>${CHAT.apT}</h4><p>${CHAT.apB}</p><div class="abtns"><div class="bdeny">${CHAT.deny}</div><div class="ballow">${CHAT.allow}</div></div></div><span class="chip ochip" style="position:absolute;left:0;top:0"><span class="ck">✓</span><span>${CHAT.allowed}</span></span></div>`, 3.95); it.push(iap);
  const ic3 = addItem(msgs, chipHTML(CHAT.c3), 5.95); it.push(ic3);
  const ib = addItem(msgs, `<div class="bb">${md(CHAT.bot)}</div>`, 6.45); it.push(ib);
  const card = $(iap.inner, '.acard'), oc = $(iap.inner, '.ochip'), allowB = $(iap.inner, '.ballow');
  iap.custom = (t, p) => {
    const col = eio(pr(t, 5.72, 6.1));
    const hc = card.offsetHeight, ho = oc.offsetHeight;
    const grow = eo5(pr(t, iap.at, iap.at + 0.4));
    iap.it.style.height = (10 + lerp(hc, ho, col)) * grow + 'px';
    card.style.opacity = clamp(p * 1.4) * (1 - clamp(col * 1.6));
    card.style.transform = `translateY(${(1 - p) * 26}px) scale(${(0.94 + 0.06 * p) * (1 - col * 0.06)})`;
    card.style.transformOrigin = '50% 0%';
    oc.style.opacity = clamp((col - 0.4) * 2);
    const press = pr(t, 5.38, 5.45) * (1 - pr(t, 5.6, 5.75));
    allowB.style.filter = `brightness(${1 - press * 0.22})`;
    allowB.style.transform = `scale(${1 - press * 0.04})`;
    // gentle attention pulse while waiting
    const pulse = t > 4.3 && t < 5.3 ? 0.5 + 0.5 * Math.sin((t - 4.3) * 7) : 0;
    card.style.boxShadow = `0 0 0 ${2 + pulse * 3}px rgba(140,143,247,${0.18 + pulse * 0.25})`;
  };
  const caps = CHAT.caps.map(c => capEl(c, L.cap)); root.append(...caps);
  const big = el(`<div class="abs" style="width:250px;height:250px;left:1625px;top:640px"></div>`); const bm = mascot(); bm.style.cssText = 'width:100%;height:100%'; big.appendChild(bm);
  if (FMT === 'h') root.append(big);
  const MST = [[-9, 'idle'], [2.15, 'thinking'], [2.85, 'working'], [3.95, 'approval'], [5.6, 'working'], [6.45, 'done']];
  return {
    root, update(t) {
      // phone motion
      const P = L.ph; const e = still ? 1 : eo5(pr(t, -0.15, 0.85));
      const dx = FMT === 'h' ? (1 - e) * 700 : 0, dy = FMT === 'h' ? 0 : (1 - e) * 900;
      const ry = FMT === 'h' ? lerp(-28, -7, e) + Math.sin(t * 0.7) * 1.5 : Math.sin(t * 0.6) * 2;
      placePhone(ph, P.cx + dx, P.cy + dy, P.s, `rotateY(${ry}deg) rotateX(${FMT === 'h' ? 2 : 1}deg)`);
      // composer typing
      const n = Math.round(CHAT.user.length * pr(t, 0.75, 1.85));
      const sent = t >= 2.0;
      const phd = $(app, '.comp .ph');
      if (!sent && n > 0) phd.innerHTML = `<span class="tx">${CHAT.user.slice(0, n)}</span><span class="caret" style="opacity:${(t * 2.2) % 1 < .6 ? 1 : 0}"></span>`;
      else phd.innerHTML = tr('Send a message', '메시지 보내기');
      // keep long text visible while typing: scroll to end
      phd.scrollLeft = 9999;
      updSend(app, t >= 2.0 && t < 6.45 ? 'stop' : (n > 0 && !sent ? 'ready' : 'idle'));
      it.forEach(x => updItem(x, t));
      updChip(ic1, t, 3.3); updChip(ic2, t, 3.85); updChip(ic3, t, 6.3);
      updStatus(app, t, 2.2, 6.45, CHAT.st.map(([a, s]) => [a + 2.2, s]));
      const [st, since] = stateAt(MST, t); setMascot(m, st, since); setMascot(bm, st, since);
      const be = eback(pr(t, 0.9, 1.5)); big.style.opacity = clamp(pr(t, 0.9, 1.1)); big.style.transform = `translateY(${(1 - be) * 120 + Math.sin(t * 1.4) * 6}px) scale(${0.5 + 0.5 * be})`;
      // finger: tap send, then tap Allow
      const [ax, ay] = centerIn(allowB, app);
      if (t < 3) updTap(app, t, [[1.7, 330, 760], [1.92, 389, 806]], 1.98);
      else updTap(app, t, [[4.85, 300, 820], [5.32, ax, ay]], 5.4);
      caps.forEach(c => updCap(c, t));
    }
  };
};

/* ---- browser ---- */
const BR = {
  user: tr('Cheapest flight to Tokyo on Dec 12?', '12월 12일 도쿄 가는 제일 싼 항공편 찾아 줘'),
  dom: 'flights.example.com', look: tr('Looking at the site', '사이트 보는 중'), doneS: tr('Done', '끝남'), view: tr('View', '보기'),
  bot: tr("Cheapest is **$182** — nonstop at 10:40 AM. Want me to book it? I'll ask before paying.", '제일 싼 건 **198,000원**, 오전 10:40 직항이에요. 예약할까요? 결제 전엔 꼭 물어볼게요.'),
  fields: KO ? [['출발', '서울 (ICN)'], ['도착', '도쿄 (NRT)'], ['날짜', '12월 12일 (금)'], ['승객', '성인 1명']] : [['From', 'Seoul (ICN)'], ['To', 'Tokyo (NRT)'], ['Date', 'Fri, Dec 12'], ['Travelers', '1 adult']],
  sorts: tr('Best|Cheapest|Fastest', '추천순|가격순|빠른순').split('|'),
  rows: [['Nimbus Air', '#5b8def', '08:05 – 10:20', tr('Nonstop · 2h 15m', '직항 · 2시간 15분'), tr('$214', '232,000원'), 2],
  ['Kite Airways', '#f2994a', '13:30 – 15:50', tr('Nonstop · 2h 20m', '직항 · 2시간 20분'), tr('$236', '256,000원'), 3],
  ['Bluefin Air', '#27ae60', '10:40 – 13:05', tr('Nonstop · 2h 25m', '직항 · 2시간 25분'), tr('$182', '198,000원'), 0],
  ['Aster Jet', '#9b51e0', '19:10 – 23:40', tr('1 stop · 4h 30m', '1회 경유 · 4시간 30분'), tr('$199', '215,000원'), 1]],
  badge: tr('Kelo is browsing', 'Kelo가 보는 중'), pick: tr('Cheapest', '최저가'),
  cap: { a: 0.2, head: tr('Uses a *real browser.*', '*진짜 브라우저*를 써요.'), sub: tr('Searches, compares and fills in forms on real sites.', '실제 사이트에서 찾고, 비교하고, 입력까지 해요.') },
};
SC.browser = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const ph = phoneEl(SBAR + HDR + `<div class="msgs"></div><div class="stl"><span class="dots"><span></span><span></span><span></span></span><span class="stt"></span></div>` + COMP + NAV(0) + TAP);
  const app = $(ph, '.app'), msgs = $(app, '.msgs');
  const m = mascot(); $(app, '.av').appendChild(m);
  const thumb = `<div class="thumb"><div style="height:14px;background:#2d3b8f"></div><div style="margin:6px 8px;height:8px;width:60%;background:#d9dbe6;border-radius:3px"></div>${[0, 1, 2].map(i => `<div style="margin:4px 8px;height:9px;border:1px solid #e3e5ee;border-radius:3px;background:${i === 0 ? '#eef0ff' : '#fff'}"></div>`).join('')}</div>`;
  const it = [];
  it.push(addItem(msgs, `<div class="bb">${CHAT.greet}</div>`, -1));
  it.push(addItem(msgs, `<div class="ub">${BR.user}</div>`, 0.2, 'r'));
  const ir = addItem(msgs, `<div class="brow"><div class="top"><div class="sq" style="background:#4b3fb5">F</div><div><div class="dom">${BR.dom}</div><div class="st"><span class="spin"></span><span class="stx">${BR.look}</span></div></div><div class="view">${BR.view}</div></div>${thumb}</div>`, 0.75); it.push(ir);
  const ib = addItem(msgs, `<div class="bb">${md(BR.bot)}</div>`, 3.0); it.push(ib);
  const bw = el(`<div class="bwin"><div class="chrome"><i></i><i></i><i></i><div class="url">${BR.dom}</div><div class="kelobadge"><span class="spin" style="border-color:#5a5cc0;border-top-color:#11122e"></span>${BR.badge}</div></div>
    <div class="site"><div class="brand">✈ flights<span style="color:#9aa0c0">.example</span></div>
    <div class="form">${BR.fields.map(f => `<div class="fld"><small>${f[0]}</small>${f[1]}</div>`).join('')}</div>
    <div class="sorts">${BR.sorts.map((s, i) => `<div class="srt ${i === 0 ? 'on' : ''}">${s}</div>`).join('')}</div>
    <div class="res">${BR.rows.map(r => `<div class="frow"><div class="air" style="background:${r[1]}"></div><div class="times">${r[2]}</div><div class="an">${r[0]} · ${r[3]}</div><div class="pickb" style="display:none;font-size:13px;font-weight:700;color:#fff;background:#5b5fe0;border-radius:8px;padding:3px 9px">${BR.pick}</div><div class="pr">${r[4]}</div></div>`).join('')}</div>
    </div>${CURSOR}</div>`);
  root.append(ph, bw);
  const cap = capEl(BR.cap, { x: 870, y: 245, w: 980, fs: 76, sfs: 29, align: 'left' }); root.append(cap);
  const rows = $$(bw, '.frow'), srts = $$(bw, '.srt'), cur = $(bw, '.cursor');
  const MST = [[-9, 'thinking'], [0.75, 'browser'], [3.0, 'done']];
  return {
    root, update(t) {
      const e = eo5(pr(t, -0.15, 0.8));
      placePhone(ph, 470 - (1 - e) * 600, 534, 1.2, `rotateY(${lerp(25, 7, e) + Math.sin(t * .7) * 1.2}deg) rotateX(2deg)`);
      const be = eo5(pr(t, 0.2, 1.0));
      bw.style.cssText = `left:860px;top:425px;width:960px;height:560px;opacity:${be};transform:translate(${(1 - be) * 160}px,${(1 - be) * 40}px) perspective(2000px) rotateY(${(1 - be) * -14}deg)`;
      const sortT = 1.55, k = eio(pr(t, sortT + 0.05, sortT + 0.5));
      rows.forEach((r, i) => {
        const y0 = i * 74, y1 = BR.rows[i][5] * 74;
        r.style.top = lerp(y0, y1, k) + 'px';
        const best = BR.rows[i][5] === 0, hl = best ? eo(pr(t, 2.1, 2.4)) : 0, sel = best ? pr(t, 2.62, 2.75) : 0;
        r.style.borderColor = hl ? `rgba(91,95,224,${hl})` : '#e3e5ee';
        r.style.boxShadow = hl ? `0 0 0 ${3 * hl}px rgba(91,95,224,${0.25 * hl}), 0 10px 26px rgba(45,59,143,${0.15 * hl})` : 'none';
        r.style.background = sel ? `rgba(238,240,255,${sel})` : '#fff';
        r.style.zIndex = best ? 2 : 1;
        $(r, '.pickb').style.display = hl > 0 ? '' : 'none'; $(r, '.pickb').style.opacity = hl;
      });
      srts.forEach((s, i) => s.classList.toggle('on', t >= sortT ? i === 1 : i === 0));
      // cursor path (window coords)
      const bestY = 54 + 22 + 30 + 16 + 62 + 14 + 36 + 12 + 32 + 22; // first row center-ish
      const pts = [[0.9, 760, 520], [1.45, srts[1].offsetLeft + 28 + srts[1].offsetWidth / 2, 54 + 22 + 30 + 16 + 62 + 14 + 18 + 22], [2.1, 600, bestY + 10], [2.55, 520, bestY]];
      let x = pts[0][1], y = pts[0][2];
      for (let i = 1; i < pts.length; i++) if (t >= pts[i - 1][0]) { const kk = eio(pr(t, pts[i - 1][0], pts[i][0])); x = lerp(pts[i - 1][1], pts[i][1], kk); y = lerp(pts[i - 1][2], pts[i][2], kk); }
      const click = (Math.abs(t - sortT) < 0.08 || Math.abs(t - 2.62) < 0.08) ? 0.85 : 1;
      cur.style.cssText = `left:${x}px;top:${y}px;opacity:${pr(t, 0.8, 1.0)};transform:scale(${click})`;
      $(bw, '.kelobadge .spin').style.transform = `rotate(${t * 540}deg)`;
      it.forEach(q => updItem(q, t));
      const dn = t >= 2.8; $(ir.inner, '.spin').style.display = dn ? 'none' : ''; $(ir.inner, '.spin').style.transform = `rotate(${t * 540}deg)`;
      $(ir.inner, '.stx').textContent = dn ? BR.doneS : BR.look;
      updStatus(app, t, 0.45, 3.0, [[0, tr('Looking at the site · flights.example.com', '사이트 보는 중 · flights.example.com')]]);
      updSend(app, t >= 0.2 && t < 3.0 ? 'stop' : 'idle');
      $(app, '.comp .ph').textContent = tr('Send a message', '메시지 보내기');
      const [st, since] = stateAt(MST, t); setMascot(m, st, since);
      updTap(app, t, [[-9, 0, 0]], -9);
      updCap(cap, t);
    }
  };
};

/* ---- connectors ---- */
const CN = {
  conn: [['GitHub', 'G', '#2f7d4a', tr('Reads your repositories, issues and PRs, and creates them when you allow it.', '내 저장소·이슈·PR을 읽고, 허락하면 만들 수도 있어요.')],
  ['Gmail', 'G', '#2f7d4a', tr('Finds and reads your Gmail, and sends mail when you allow it.', 'Gmail을 찾아 읽고, 허락하면 메일을 보내요.')],
  ['Google Calendar', 'GC', '#8a2b52', tr('Sees your schedule and adds, moves or deletes events. Each change is asked first.', '일정을 보고, 새로 넣거나 옮기거나 지워요. 바꿀 때마다 먼저 물어봐요.')],
  ['Notion', 'N', '#7a2e8a', tr('Finds and edits pages and databases.', '페이지와 데이터베이스를 찾고 고쳐요.')]],
  pop: [['Google Drive', 'GD', '#2d6b7a', tr('Works with your Google Drive, Docs, Sheets and Slides.', 'Google 드라이브, 문서, 시트, 슬라이드와 함께 써요.'), 2.75],
  ['Slack', 'S', '#4a8a2e', tr('Finds and reads messages, and sends them when you allow it.', '메시지를 찾고 읽고, 허락하면 보내요.'), 3.45],
  ['Outlook Mail', 'OM', '#8a3a2a', tr('Uses Outlook mail, calendar and contacts.', 'Outlook 메일·캘린더·연락처를 써요.')],
  ['Outlook Calendar', 'OC', '#2e7d5a', tr('Uses Outlook mail, calendar and contacts.', 'Outlook 메일·캘린더·연락처를 써요.')],
  ['Zoom', 'Z', '#3d7f3d', tr('Sets up and changes meetings, and sees recordings.', '회의를 잡고 바꾸고, 녹화를 봐요.')]],
  on: tr('Connected', '연결됨'), off: tr('Connect', '연결하기'),
  cap: { a: 0.2, head: tr('Works with the apps | *you already use.*', '쓰던 서비스에 | *바로 연결.*'), sub: tr('Google Drive, Slack, Notion, GitHub, Outlook and more.', 'Google Drive, Slack, Notion, GitHub, Outlook 등.') },
  tiles: [['Google Drive', 'GD', '#2d6b7a'], ['Slack', 'S', '#4a8a2e'], ['Notion', 'N', '#7a2e8a'], ['GitHub', 'G', '#2f7d4a'], ['Outlook Mail', 'OM', '#8a3a2a'], ['Google Calendar', 'GC', '#8a2b52']],
};
SC.connectors = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const row = (c, off) => `<div class="crow"><div class="sq" style="background:${c[2]}">${c[1]}</div><div class="tx"><div class="nm">${c[0]}</div><div class="ds">${c[3]}</div></div><div class="pillst ${off ? 'p-off' : 'p-on'}">${off ? CN.off : CN.on}</div></div>`;
  const ph = phoneEl(SBAR + HDR + `<div class="scrl"><div class="inner"><div class="search">${tr('Search', '검색')}</div><div class="sec">${tr('Connected', '연결됨')}</div><div class="group">${CN.conn.map(c => row(c, false)).join('')}</div><div class="sec">${tr('Popular services', '많이 쓰는 서비스')}</div><div class="group">${CN.pop.map(c => row(c, true)).join('')}</div></div></div>` + NAV(2) + TAP);
  const app = $(ph, '.app'); const m = mascot(); $(app, '.av').appendChild(m);
  const inner = $(app, '.scrl .inner');
  const pills = $$(app, '.group')[1].querySelectorAll('.pillst');
  const tfs = { h: 20, v: 25, gp: 24, gl: 18 }[FMT];
  const TP = {
    h: [[1010, 215], [1740, 185], [1755, 455], [985, 700], [1745, 760], [1030, 935]],
    v: [[215, 760], [870, 850], [180, 1150], [905, 1290], [230, 1590], [860, 1730]],
    gp: [[245, 560], [960, 650], [215, 920], [990, 1060], [250, 1300], [950, 1390]],
    gl: [[300, 300], [1300, 300], [250, 600], [1350, 600], [400, 800], [1200, 800]],
  }[FMT];
  const tiles = CN.tiles.map((c, i) => { const d = el(`<div class="tile" style="font-size:${tfs}px"><div class="sq" style="background:${c[2]};width:${tfs * 2.2}px;height:${tfs * 2.2}px;font-size:${tfs * 0.8}px">${c[1]}</div>${c[0]}<span style="color:#7fdc9b;margin-left:4px">${IC.check(tfs)}</span></div>`); root.appendChild(d); return d; });
  root.prepend(ph);
  const cap = capEl(CN.cap, L.cap); root.append(cap);
  return {
    root, update(t) {
      const P = L.ph, e = GIF || FMT === 'v' ? 1 : eo5(pr(t, -0.15, 0.8));
      placePhone(ph, P.cx + (FMT === 'h' ? (1 - e) * 600 : 0), P.cy + (FMT === 'h' ? 0 : (1 - e) * 900), P.s, `rotateY(${FMT === 'h' ? -7 + Math.sin(t * .7) * 1.5 : Math.sin(t * .6) * 2}deg)`);
      const sc = eio(pr(t, 0.9, 2.2));
      inner.style.transform = `translateY(${-330 * sc}px)`;
      const conn = (i) => CN.pop[i][4];
      [0, 1].forEach(i => {
        const p = pills[i], ct = conn(i);
        if (t >= ct + 0.35) { p.className = 'pillst p-on'; p.innerHTML = CN.on; }
        else if (t >= ct) { p.className = 'pillst p-off'; p.innerHTML = `<span class="spin" style="width:11px;height:11px;transform:rotate(${t * 540}deg)"></span>`; }
        else { p.className = 'pillst p-off'; p.innerHTML = CN.off; }
        const pop = t >= ct + 0.35 ? 1 + 0.12 * Math.sin(Math.PI * pr(t, ct + 0.35, ct + 0.65)) : 1;
        p.style.transform = `scale(${pop})`;
      });
      // finger taps on the two Connect pills
      const [x0, y0] = centerIn(pills[0], app), [x1, y1] = centerIn(pills[1], app);
      const ty = -330 * sc; // pills are inside a translated container; offsets ignore transforms
      if (t < 3.1) updTap(app, t, [[2.3, 260, 640], [2.68, x0, y0 + ty]], 2.72);
      else updTap(app, t, [[3.12, x0, y0 + ty], [3.4, x1, y1 + ty]], 3.42);
      const [st, since] = stateAt([[-9, 'idle'], [1.0, 'reading'], [3.1, 'done']], t); setMascot(m, st, since);
      if (FMT !== 'h') {
        let x = 40 - t * 70; const y = FMT === 'v' ? 545 : 410;
        tiles.forEach((d, i) => {
          const a = 0.3 + i * 0.1, p = eo5(pr(t, a, a + 0.5));
          d.style.left = x + 'px'; d.style.top = y + 'px'; x += d.offsetWidth + 18;
          d.style.opacity = p; d.style.transform = `translateY(-50%) translateY(${(1 - p) * 40}px)`;
        });
        updCap(cap, t); return;
      }
      tiles.forEach((d, i) => {
        const a = 0.35 + i * 0.13, p = eback(pr(t, a, a + 0.55)), f = Math.sin(t * 1.3 + i * 1.7) * 7;
        const [x, y] = TP[i];
        d.style.left = x + 'px'; d.style.top = y + 'px';
        d.style.opacity = clamp(pr(t, a, a + 0.25));
        d.style.transform = `translate(-50%,-50%) translateY(${f + (1 - p) * 60}px) scale(${0.6 + 0.4 * p}) rotate(${(i % 2 ? 1 : -1) * 2.5}deg)`;
      });
      updCap(cap, t);
    }
  };
};

/* ---- keychain (horizontal ad only) ---- */
SC.keychain = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const rows = [['github.com', 'G', '#2f7d4a', 'alex@example.com'], ['mail.example.com', 'M', '#2b5fae', 'alex@example.com'], ['shop.example.com', 'S', '#a5503a', 'alex']];
  const LOCK = `<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="#11122e" stroke-width="2.2" stroke-linecap="round"><path class="shk" d="M8 10.5V8a4 4 0 0 1 8 0v2.5"/><rect x="5" y="10.5" width="14" height="10" rx="2.5" fill="#11122e" stroke="none"/><circle cx="12" cy="15.5" r="1.6" fill="#a9acff" stroke="none"/></svg>`;
  const card = el(`<div class="fcard kc" style="left:1010px;top:300px;width:760px;padding:34px 40px 22px">
    <div style="display:flex;align-items:center;gap:22px;margin-bottom:12px"><div class="lk" style="width:84px;height:84px;border-radius:24px;background:var(--lav);display:flex;align-items:center;justify-content:center">${LOCK}</div>
    <div><div style="font-size:36px;font-weight:800;letter-spacing:-0.02em">${tr('Kelo Keychain', 'Kelo 키체인')}</div><div style="font-size:21px;color:var(--a-mute)">${tr('Stored on your Kelo server', 'Kelo 서버에 보관')}</div></div></div>
    ${rows.map(r => `<div class="row"><div class="sq" style="background:${r[2]};width:52px;height:52px;border-radius:13px;font-size:20px">${r[1]}</div><div style="flex:1"><div style="font-size:23px;font-weight:700">${r[0]}</div><div style="font-size:18px;color:var(--a-mute)">${r[3]}</div></div><div class="dots2" style="font-size:26px;letter-spacing:3px;color:#cfd0ff;font-family:JBM,monospace"></div><span style="color:#7fdc9b">${IC.check(26)}</span></div>`).join('')}
  </div>`);
  const mo = el(`<div style="position:absolute;width:190px;height:190px"></div>`); const m = mascot(); m.style.cssText = 'width:100%;height:100%'; mo.appendChild(m);
  const cap = capEl({ a: 0.2, head: tr('Your logins, | *locked away.*', '로그인 정보는 | *키체인에 안전하게.*'), sub: tr('Kept in the Kelo Keychain on your Kelo server. Nobody else can read them.', 'Kelo 서버의 키체인에만 보관돼요. 다른 누구도 볼 수 없어요.') }, Object.assign({}, L.cap, { w: 760 }));
  root.append(card, mo, cap);
  const rws = $$(card, '.row');
  return {
    root, update(t) {
      const e = eo5(pr(t, -0.1, 0.7));
      card.style.opacity = e; card.style.transform = `translateY(${(1 - e) * 80}px) perspective(2000px) rotateY(${-6 + (1 - e) * -12}deg)`;
      rws.forEach((r, i) => {
        const a = 0.45 + i * 0.16, p = eo5(pr(t, a, a + 0.5));
        r.style.opacity = p; r.style.transform = `translateX(${(1 - p) * 40}px)`;
        const n = Math.round(10 * pr(t, a + 0.2, a + 0.75));
        $(r, '.dots2').textContent = '•'.repeat(n);
        $(r, 'span').style.opacity = pr(t, a + 0.75, a + 0.9);
      });
      const lk = pr(t, 1.55, 1.75), glow = Math.max(0, 1 - Math.abs(t - 1.8) * 2);
      $(card, '.shk').style.transform = `translateY(${(1 - eback(lk)) * -3}px)`;
      $(card, '.lk').style.boxShadow = `0 0 0 ${glow * 14}px rgba(140,143,247,${glow * 0.35})`;
      const mp = eback(pr(t, 0.6, 1.2));
      mo.style.left = '1700px'; mo.style.top = '712px'; mo.style.opacity = clamp(pr(t, 0.6, 0.8));
      mo.style.transform = `scale(${mp})`;
      const [st, since] = stateAt([[-9, 'reading'], [1.75, 'done']], t); setMascot(m, st, since);
      updCap(cap, t);
    }
  };
};

/* ---- goals + schedules ---- */
const GL = {
  title: tr('Lose weight', '살 빼기'), edit: tr('Edit', '고치기'), resume: tr('Let Kelo continue', 'Kelo에게 이어서 맡기기'),
  facts: tr('What you\'ve shared', '알려 주신 것'), plan: tr('Plan', '계획'),
  F: KO ? [['목표 감량', '5kg, 천천히'], ['집중', '식단과 운동 같이'], ['좋아하는 운동', '웨이트 트레이닝'], ['빈도', '주 3~4회']] : [['Target loss', '5 kg, steady pace'], ['Focus', 'Diet and exercise together'], ['Preferred workout', 'Weight training'], ['Frequency', '3–4 times a week']],
  S: KO ? [['주간 운동 루틴 만들고 캘린더에 넣기', 'Alex의 한 주에 맞춘 루틴, Google 캘린더와 연동', 'k'], ['체중 재고 지금 식단 돌아보기', '오늘 몸무게를 적고 평소 식사를 살펴봐요', 'm'], ['주 3~4회 운동하기', '캘린더에 잡힌 일정대로 운동해요', 'm'], ['주간 점검과 피드백', '한 주 운동을 돌아보고 다음 주 루틴을 고쳐요', 'k']]
    : [['Build a weekly workout routine and add it to the calendar', "A routine that fits Alex's week, synced to Google Calendar", 'k'], ['Weigh in and review current meals', "Record today's weight and look at usual eating habits", 'm'], ['Work out 3–4 times a week', 'Follow the sessions on your calendar', 'm'], ['Weekly check-in and feedback', "Look back at the week's workouts and adjust next week's routine", 'k']],
  forK: tr('For Kelo', 'Kelo가 할 일'), forM: tr('For me', '내가 할 일'), doing: tr('Doing', '하는 중'), done: tr('Done', '끝남'), todo: tr('To do', '할 일'),
  planN: n => tr(`Plan ${n}/4`, `계획 ${n}/4`),
  schedT: tr('Schedules', '예약'),
  sched: KO ? [['아침 브리핑', '평일 오전 7:30'], ['주간 점검', '매주 일 오후 8:00']] : [['Morning briefing', 'Weekdays · 7:30 AM'], ['Weekly check-in', 'Sundays · 8:00 PM']],
  caps: [{ a: 0.2, b: 2.55, head: tr('Set a goal. | Kelo *keeps you on it.*', '다짐을 세우면, | *끝까지 챙겨요.*'), sub: tr('It plans the steps and checks in with you.', '계획을 짜고, 때맞춰 확인해요.') },
  { a: 2.55, head: tr('Runs on *schedule.*', '정해 둔 시간엔 *알아서.*'), sub: tr('Morning briefings, weekly check-ins — even while you sleep.', '아침 브리핑, 주간 점검까지. 자는 동안에도요.') }],
};
SC.goals = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const steps = GL.S.map((s, i) => `<div class="step"><div class="ico">${s[2] === 'k' ? `<span class="sp" style="color:var(--lav2)">${IC.spark(20)}</span>` : '<div class="cbx"></div>'}</div><div><div class="nm">${s[0]}</div><div class="ds">${s[1]}</div><span class="tag t-lav">${s[2] === 'k' ? GL.forK : GL.forM}</span>${s[2] === 'k' ? `<span class="tag st ${i === 0 ? 't-gray' : 't-gray'}">${i === 0 ? GL.doing : GL.todo}</span>` : ''}</div></div>`).join('');
  const ph = phoneEl(SBAR + `<div class="gtop"><span>${IC.back(24)}</span><span>${GL.title}</span><span class="ed">${GL.edit}</span></div><div class="gbody"><span class="tag t-green pn">${GL.planN(0)}</span><div class="gbtn">${GL.resume}</div><div class="gsec">${GL.facts}</div>${GL.F.map(f => `<div class="fact"><div class="k">${f[0]}</div><div>${f[1]}</div></div>`).join('')}<div class="gsec" style="margin-top:16px">${GL.plan}</div>${steps}</div>` + NAV(1) + TAP);
  const app = $(ph, '.app');
  const fs = { h: 1, v: 1.32, gp: 1.18, gl: 1 }[FMT];
  const card = el(`<div class="fcard" style="width:${430 * fs}px;padding:${22 * fs}px ${24 * fs}px ${12 * fs}px">
    <div style="display:flex;align-items:center;gap:${12 * fs}px;margin-bottom:${6 * fs}px"><div class="sm" style="width:${64 * fs}px;height:${64 * fs}px;border-radius:50%;background:#fff3d6;flex:none;display:flex;align-items:center;justify-content:center"></div><div style="font-size:${26 * fs}px;font-weight:800">${GL.schedT}</div></div>
    ${GL.sched.map(s => `<div style="display:flex;align-items:center;gap:${14 * fs}px;padding:${13 * fs}px 0;border-top:1px solid #2e2e36"><span style="color:var(--lav2)">${IC.clock(26 * fs)}</span><div style="flex:1"><div style="font-size:${20 * fs}px;font-weight:700">${s[0]}</div><div style="font-size:${16 * fs}px;color:var(--a-mute)">${s[1]}</div></div><div style="width:${46 * fs}px;height:${28 * fs}px;border-radius:${14 * fs}px;background:var(--lav);position:relative"><div style="position:absolute;right:${3 * fs}px;top:${3 * fs}px;width:${22 * fs}px;height:${22 * fs}px;border-radius:50%;background:#11122e"></div></div></div>`).join('')}
  </div>`);
  const sm = mascot(); sm.style.cssText = `width:${56 * fs}px;height:${56 * fs}px`; $(card, '.sm').appendChild(sm);
  root.append(ph, card);
  const caps = GL.caps.map(c => capEl(c, L.cap)); root.append(...caps);
  const st0 = $$(app, '.step')[0];
  const CP = { h: [900, 700], v: [70, 1450], gp: [60, 1130], gl: [100, 500] }[FMT];
  return {
    root, update(t) {
      const P = L.ph, e = GIF || FMT === 'v' ? 1 : eo5(pr(t, -0.15, 0.8));
      placePhone(ph, P.cx + (FMT === 'h' ? (1 - e) * 600 : 0), P.cy + (FMT === 'h' ? 0 : (1 - e) * 900), P.s, `rotateY(${FMT === 'h' ? -7 + Math.sin(t * .7) * 1.5 : Math.sin(t * .6) * 2}deg)`);
      const done = t >= 1.5, k = pr(t, 1.5, 1.8);
      const ico = $(st0, '.ico');
      if (done) { ico.innerHTML = `<div style="width:20px;height:20px;border-radius:5px;background:#3fbf73;color:#0d2016;display:flex;align-items:center;justify-content:center;transform:scale(${1 + 0.35 * Math.sin(Math.PI * k)})">${IC.check(15)}</div>`; }
      else { ico.innerHTML = `<span style="color:var(--lav2);display:inline-block;transform:rotate(${t * 120}deg)">${IC.spark(20)}</span>`; }
      $(st0, '.nm').style.cssText = done ? `color:#8d8d96;text-decoration:line-through;text-decoration-color:rgba(141,141,150,${k})` : '';
      const tg = $(st0, '.st'); tg.textContent = done ? GL.done : GL.doing; tg.className = 'tag st ' + (done ? 't-green' : 't-gray');
      $(app, '.pn').textContent = GL.planN(done ? 1 : 0);
      const ce = eback(pr(t, 2.6, 3.2));
      card.style.left = CP[0] + 'px'; card.style.top = CP[1] + 'px';
      card.style.opacity = clamp(pr(t, 2.6, 2.85));
      card.style.transform = `translateY(${(1 - ce) * 70}px) scale(${0.85 + 0.15 * ce})`;
      setMascot(sm, 'sleeping', 9);
      updTap(app, t, [[-9, 0, 0]], -9);
      caps.forEach(c => updCap(c, t));
    }
  };
};

/* ---- install ---- */
const BIRD = ['......GG......', '.....GG.......', '....BBBBBB....', '...BBBBBBBB...', '..BBBKBBKBBB..', '..BBBBOOBBBB..', '.BBBLLLLLLBBB.', '.BBLLLLLLLLBB.', '..BLLLLLLLLB..', '..BBLLLLLLBB..', '...BBBBBBBB...', '....F....F....'];
const PIXC = { G: '#66bb6a', B: '#29b6f6', L: '#b3e5fc', K: '#15151a', O: '#ffb300', F: '#9aa3c7' };
function birdPix(px) {
  let cells = '';
  BIRD.forEach(line => [...line].forEach(ch => { cells += `<i style="width:${px}px;height:${px}px;background:${PIXC[ch] || 'transparent'}"></i>`; }));
  return `<span class="pix" style="grid-template-columns:repeat(14,${px}px)">${cells}</span>`;
}
SC.install = (dur) => {
  const root = el(`<div class="scene"></div>`);
  const cfg = { h: { x: 330, y: 340, w: 1260, h: 600, fs: 26, cy: 180 }, v: { x: 40, y: 640, w: 1000, h: 720, fs: 27, cy: 360 }, gl: { x: 160, y: 240, w: 1280, h: 570, fs: 25, cy: 120 }, gp: { x: 40, y: 470, w: 1120, h: 700, fs: 26, cy: 240 } }[FMT];
  const CMD = 'curl -fsSL https://kelo.chat/install.sh | bash';
  const A = 'https://alex.kelo.chat';
  const lines = [
    `<div class="ln"><span class="pr-s">$</span> <span class="cm cmd"></span><span class="caret tc" style="height:1.05em;vertical-align:-0.15em"></span></div>`,
    `<div class="ln"></div>`,
    `<div class="L" data-a="2.0" style="display:flex;gap:1.1em;align-items:center;margin-bottom:.5em">${birdPix(Math.round(cfg.fs * 0.3))}<div><div style="color:#fff;font-weight:700">${tr('Kelo installer', 'Kelo 설치 프로그램')}</div><div class="dim">${tr('Your personal assistant, on a server you run.', '내가 운영하는 서버에서 돌아가는 나만의 비서.')}</div></div></div>`,
    `<div class="ln L" data-a="2.3"><span class="lavc">›</span> Docker</div>`,
    `<div class="ln L" data-a="2.4">  <span class="ok">✓</span> ${tr('Docker is running', 'Docker 실행 중')}</div>`,
    `<div class="ln L" data-a="2.65">  <span class="ok">✓</span> ${tr('Kelo files ready', 'Kelo 파일 준비 완료')}</div>`,
    `<div class="ln L" data-a="2.9">  <span class="ok sv">✓</span> ${tr('Kelo server started', 'Kelo 서버 시작')}</div>`,
    `<div class="ln L" data-a="3.5">  <span class="ok">✓</span> ${tr('Default address ready:', '기본 제공 도메인 준비:')} <span class="lavc">${A}</span></div>`,
    `<div class="L" data-a="3.9"><div class="tbox"><div><span class="ok" style="font-weight:700">✓ ${tr('All set', '준비 완료')}</span>   <span class="cm">${A}</span></div><div class="dim">→ ${tr('Open the Kelo app on your phone to connect.', '휴대폰에서 Kelo 앱을 열어 연결하세요.')}</div></div></div>`,
  ];
  const term = el(`<div class="term" style="left:${cfg.x}px;top:${cfg.y}px;width:${cfg.w}px;height:${cfg.h}px"><div class="bar"><i></i><i></i><i></i><span>kelo — bash</span></div><div class="body" style="font-size:${cfg.fs}px">${lines.join('')}</div></div>`);
  const cap = capEl({ a: 0.1, head: FMT === 'h' || FMT === 'gl' ? tr('One command. *Your own Kelo server.*', '명령어 한 줄이면 *Kelo 서버 완성.*') : tr('One command. | *Your own Kelo server.*', '명령어 한 줄이면 | *Kelo 서버 완성.*'), sub: tr('Runs anywhere Docker runs. Then connect your phone.', 'Docker만 있으면 어디든 설치하고, 휴대폰을 연결하면 끝.') },
    { x: 80, y: cfg.cy, w: W - 160, fs: { h: 80, v: 80, gl: 60, gp: 66 }[FMT], sfs: { h: 30, v: 36, gl: 27, gp: 30 }[FMT], align: 'center' });
  root.append(term, cap);
  const Ls = $$(term, '.L'), sv1 = $(term, '.sv');
  const ks = Math.max(1, 5.0 / dur); // shorter cuts play the install a little faster so "All set" still holds for a second
  return {
    root, update(t0) {
      const t = t0 * ks;
      const e = eo5(pr(t, -0.1, 0.7));
      term.style.opacity = GIF ? 1 : e; term.style.transform = `translateY(${GIF ? 0 : (1 - e) * 70}px) scale(${GIF ? 1 : 0.96 + 0.04 * e})`;
      const n = Math.round(CMD.length * pr(t, 0.35, 1.7));
      $(term, '.cmd').textContent = CMD.slice(0, n);
      $(term, '.tc').style.opacity = t < 1.9 && (t * 2.2) % 1 < .6 ? 1 : 0;
      Ls.forEach(l => { const a = +l.dataset.a, p = eo(pr(t, a, a + 0.25)); l.style.opacity = p; l.style.transform = `translateY(${(1 - p) * 10}px)`; l.style.visibility = t >= a ? 'visible' : 'hidden'; });
      sv1.textContent = t < 3.3 ? '⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'[Math.floor(t * 14) % 10] : '✓';
      const bx = $(term, '.tbox'), bp = pr(t, 3.9, 4.4); bx.style.boxShadow = `0 0 0 ${bp * 2}px rgba(110,231,168,${0.4 * (1 - pr(t, 4.4, 5.2))})`;
      bx.style.borderColor = `rgba(110,231,168,${0.3 + 0.5 * bp})`;
      updCap(cap, t);
    }
  };
};

/* ---- end card / hero ---- */
SC.end = (dur, prevOrHero = false) => {
  const hero = prevOrHero === true;
  const root = el(`<div class="scene"></div>`);
  const c = { h: { my: 300, ms: 300, wy: 560, wf: 168, ty: 690, tf: 44, uy: 800, uf: 38, sy: 890, sf: 24 }, v: { my: 560, ms: 420, wy: 930, wf: 210, ty: 1110, tf: 54, uy: 1320, uf: 46, sy: 1430, sf: 30 }, gl: { my: 225, ms: 250, wy: 455, wf: 140, ty: 568, tf: 40, uy: 670, uf: 34, sy: 750, sf: 22 }, gp: { my: 400, ms: 340, wy: 720, wf: 180, ty: 880, tf: 48, uy: 1080, uf: 40, sy: 1170, sf: 28 } }[FMT];
  const m = mascot('em'); m.style.cssText = `position:absolute;width:${c.ms}px;height:${c.ms}px;left:${W / 2 - c.ms / 2}px;top:${c.my - c.ms / 2}px`;
  const ring = el(`<div class="abs" style="left:${W / 2 - c.ms * 0.62}px;top:${c.my - c.ms * 0.62}px;width:${c.ms * 1.24}px;height:${c.ms * 1.24}px;border-radius:50%;background:radial-gradient(circle, rgba(140,143,247,.28), rgba(79,195,247,.10) 50%, transparent 70%)"></div>`);
  const word = el(`<div class="abs word" style="left:0;right:0;text-align:center;font-size:${c.wf}px;top:${c.wy - c.wf * 0.55}px">Kelo</div>`);
  const tag = el(`<div class="abs" style="left:60px;right:60px;text-align:center;font-size:${c.tf}px;font-weight:600;color:#d9d9e6;top:${c.ty - c.tf * 0.6}px;letter-spacing:-0.01em">${FMT === 'v' || FMT === 'gp' ? tr('Your server. Your model.<br>Your assistant.', '서버도 모델도 내가 고르는<br>나만의 AI 비서') : tr('Your server. Your model. Your assistant.', '서버도 모델도 내가 고르는, 나만의 AI 비서')}</div>`);
  const url = el(`<div class="abs" style="left:0;right:0;text-align:center;top:${c.uy - c.uf * 0.9}px"><span class="urlpill" style="font-size:${c.uf}px;padding:${c.uf * 0.32}px ${c.uf * 0.8}px">kelo.chat</span></div>`);
  const sub = el(`<div class="abs" style="left:0;right:0;text-align:center;font-size:${c.sf}px;color:var(--mute);font-weight:600;letter-spacing:.06em;top:${c.sy - c.sf * 0.6}px">${tr('Self-hosted  ·  Local models too  ·  Android app', '셀프 호스팅  ·  로컬 모델 지원  ·  Android 앱')}</div>`);
  root.append(ring, m, word, tag, url, sub);
  if (hero) sub.style.display = 'none';
  return {
    root, update(t) {
      if (hero) {
        const states = ['idle', 'thinking', 'approval', 'done'];
        const seg = 1.8, i = Math.floor(t / seg) % 4;
        setMascot(m, states[i], t - Math.floor(t / seg) * seg);
        ring.style.transform = `scale(${1 + 0.05 * Math.sin(t * Math.PI * 2 / 3.6)})`;
        $(url, '.urlpill').style.boxShadow = `0 0 ${20 + 14 * Math.sin(t * Math.PI * 2 / 3.6)}px rgba(140,143,247,.35)`;
        return;
      }
      const mp = eback(pr(t, 0.05, 0.7));
      m.style.opacity = clamp(pr(t, 0.05, 0.25));
      const [st, since] = stateAt([[-9, 'done'], [1.8, 'idle']], t); setMascot(m, st, since);
      m.style.transform = `translateY(${(1 - mp) * 120}px) scale(${(0.4 + 0.6 * mp) * (since < 0.45 && t > 1.8 ? 1 + 0.1 * Math.sin(Math.PI * since / 0.45) : 1)})`;
      ring.style.opacity = pr(t, 0.2, 0.8); ring.style.transform = `scale(${0.6 + 0.4 * eo(pr(t, 0.2, 1.0)) + 0.04 * Math.sin(t * 2)})`;
      [[word, 0.35], [tag, 0.6], [url, 0.85], [sub, 1.05]].forEach(([e2, a]) => { const p = eo5(pr(t, a, a + 0.7)); e2.style.opacity = p; e2.style.transform = `translateY(${(1 - p) * 40}px)`; e2.style.filter = `blur(${(1 - p) * 8}px)`; });
      $(url, '.urlpill').style.boxShadow = `0 0 ${24 * pr(t, 1.2, 1.8)}px rgba(140,143,247,.4)`;
    }
  };
};

/* ---- poster / thumbnail ---- */
SC.poster = (dur) => {
  const c = SC.chat(dur);
  $$(c.root, '.cap').forEach(x => { x.style.visibility = 'hidden'; x.className = 'capx'; });
  const V = FMT === 'v';
  const box = el(`<div class="abs" style="${V ? 'left:60px;right:60px;top:96px;text-align:center' : 'left:150px;width:860px;top:50%;transform:translateY(-50%)'}">
    <div style="display:inline-flex;align-items:center;gap:14px;font-size:${V ? 30 : 26}px;font-weight:700;color:var(--lav2);letter-spacing:.04em;border:1.5px solid rgba(169,172,255,.4);border-radius:999px;padding:8px 22px 8px 10px;background:rgba(140,143,247,.10)"><span class="pm" style="width:${V ? 46 : 40}px;height:${V ? 46 : 40}px;display:inline-block"></span>${tr('Self-hosted AI agent', '셀프 호스팅 AI 에이전트')}</div>
    <div style="font-size:${V ? 92 : 104}px;font-weight:850;letter-spacing:-0.035em;line-height:1.04;margin-top:${V ? 26 : 30}px">${tr('Your server.<br>Your model.<br><span class="acc">Your assistant.</span>', V ? '서버도 모델도<br><span class="acc">내가 고르는 AI 비서</span>' : '서버도 모델도<br><span class="acc">내가 고르는<br>AI 비서</span>')}</div>
    <div style="display:${V ? 'none' : 'block'};font-size:32px;color:var(--mute);font-weight:500;line-height:1.4;margin-top:26px">${tr('Runs on a server you choose — a Mac at home, a NAS or the cloud — with the AI model you pick, local ones included.', '집에 있는 Mac, NAS, 클라우드 서버 등 원하는 곳에 두고,<br>로컬 모델까지 원하는 AI 모델로 써요.')}</div>
    <div style="margin-top:${V ? 26 : 34}px"><span class="urlpill" style="font-size:${V ? 38 : 36}px;padding:${V ? 9 : 12}px 32px">kelo.chat</span></div></div>`);
  c.root.appendChild(box);
  const pm = mascot(); pm.style.cssText = 'width:100%;height:100%'; $(box, '.pm').appendChild(pm);
  return { root: c.root, update(t) { c.update(4.6); setMascot(pm, 'idle', 9); } };
};

/* ================= timeline ================= */
// window.PLANS comes from plans.js (shared with audio.py). ?scene=NAME in a video format plays that one scene at its video length.
let plan;
if (GIF) plan = PLANS.gif[ONLY || 'chat'];
else if (ONLY === 'poster') plan = [['poster', 1]];
else if (ONLY) plan = PLANS[FMT].filter(x => x[0] === ONLY);
else plan = PLANS[FMT];
// background glows
const bg = el(`<div class="bgl"><div class="glow g1"></div><div class="glow g2"></div><div class="glow g3"></div></div>`);
stage.appendChild(bg);
const scenes = []; let acc = 0;
for (const [i, [name, dur]] of plan.entries()) {
  const s = name === 'hero' ? SC.end(dur, true) : SC[name](dur, i ? plan[i - 1][0] : null);
  s.name = name; s.start = acc; s.dur = dur; acc += dur; stage.appendChild(s.root); scenes.push(s);
}
window.DURATION = acc;
const SW = Math.max(W, H);
function updBg(t) {
  const [g1, g2, g3] = $$(bg, '.glow');
  const a = GIF ? 0.55 : 1;
  g1.style.cssText = `width:${SW * 0.55}px;height:${SW * 0.55}px;left:${W * 0.62 - SW * 0.275 + Math.sin(t * 0.25) * W * 0.06}px;top:${H * 0.35 - SW * 0.275 + Math.cos(t * 0.21) * H * 0.06}px;background:rgba(140,143,247,${0.22 * a})`;
  g2.style.cssText = `width:${SW * 0.45}px;height:${SW * 0.45}px;left:${W * 0.18 - SW * 0.225 + Math.cos(t * 0.3) * W * 0.05}px;top:${H * 0.78 - SW * 0.225 + Math.sin(t * 0.23) * H * 0.05}px;background:rgba(79,195,247,${0.13 * a})`;
  g3.style.cssText = `width:${SW * 0.3}px;height:${SW * 0.3}px;left:${W * 0.9 - SW * 0.15}px;top:${H * 0.95 - SW * 0.15 + Math.sin(t * 0.4) * 30}px;background:rgba(102,187,106,${0.07 * a})`;
}
window.render = (t) => {
  updBg(t);
  scenes.forEach((s, i) => {
    const tau = t - s.start, first = i === 0, last = i === scenes.length - 1;
    const pre = first ? 0 : 0.3, post = last ? 0 : 0.3;
    if (tau < -pre || tau > s.dur + post) { s.root.style.display = 'none'; return; }
    s.root.style.display = 'block';
    const inP = first ? 1 : eo(pr(tau, -0.3, 0.2)), outQ = last ? 0 : eio(pr(tau, s.dur - 0.25, s.dur + 0.3));
    s.root.style.opacity = inP * (1 - outQ);
    s.root.style.transform = outQ > 0 ? `translateY(${-50 * outQ}px) scale(${1 + 0.03 * outQ})` : (inP < 1 ? `scale(${0.985 + 0.015 * inP})` : '');
    s.root.style.filter = outQ > 0 ? `blur(${outQ * 10}px)` : (inP < 1 ? `blur(${(1 - inP) * 8}px)` : '');
    s.update(tau);
  });
  document.getAnimations().forEach(a => { a.pause(); a.currentTime = t * 1000; });
};
window.SCENES = scenes.map(s => [s.name, s.start, s.dur]);
window.READY = document.fonts.ready.then(() => { render(0); return true; });

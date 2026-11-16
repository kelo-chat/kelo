/* ---- poster / thumbnail ---- */
SC.poster = (dur) => {
  const c = SC.chat(dur);
  $$(c.root, '.cap').forEach(x => { x.style.visibility = 'hidden'; x.className = 'capx'; });
  const V = FMT === 'v';
  const box = el(`<div class="abs" style="${V ? 'left:60px;right:60px;top:110px;text-align:center' : 'left:150px;width:860px;top:50%;transform:translateY(-50%)'}">
    <div style="display:inline-flex;align-items:center;gap:14px;font-size:${V ? 30 : 26}px;font-weight:700;color:var(--lav2);letter-spacing:.04em;border:1.5px solid rgba(169,172,255,.4);border-radius:999px;padding:8px 22px 8px 10px;background:rgba(140,143,247,.10)"><span class="pm" style="width:${V ? 46 : 40}px;height:${V ? 46 : 40}px;display:inline-block"></span>${tr('Self-hosted AI agent', '셀프 호스팅 AI 에이전트')}</div>
    <div style="font-size:${V ? 96 : 104}px;font-weight:850;letter-spacing:-0.035em;line-height:1.04;margin-top:30px">${tr('The AI that<br><span class="acc">gets it done.</span>', '말만 하지 않고<br><span class="acc">직접 해내는 AI</span>')}</div>
    <div style="font-size:${V ? 34 : 32}px;color:var(--mute);font-weight:500;line-height:1.4;margin-top:26px">${tr('Chats, browses, connects your apps — and asks before it acts. On your own Kelo server.', '대화하고, 브라우저를 쓰고, 쓰던 서비스에 연결해요.<br>실행 전엔 꼭 물어봐요. Kelo 서버에 직접 설치해서 써요.')}</div>
    <div style="margin-top:34px"><span class="urlpill" style="font-size:${V ? 42 : 36}px;padding:12px 32px">kelo.chat</span></div></div>`);
  c.root.appendChild(box);
  const pm = mascot(); pm.style.cssText = 'width:100%;height:100%'; $(box, '.pm').appendChild(pm);
  return { root: c.root, update(t) { c.update(4.6); setMascot(pm, 'idle', 9); } };
};


import { useEffect, useRef, useState } from "react";
import { ApiError, api, type OAuthFlow, type OAuthProvider } from "./api";
import { ShieldIcon } from "./icons";

const mmss = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Connect a service by typing a short code on its own page. The app shows the code, the user types it on the service (on any device), and the
 * app receives a token that goes straight into the vault: it is never shown, and the assistant never sees it.
 */
export function DeviceConnect({ id, onChanged, onNotify }: { id: string; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [p, setP] = useState<OAuthProvider | null>(null);
  const [flow, setFlow] = useState<OAuthFlow | null>(null);
  const [deadline, setDeadline] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [clientId, setClientId] = useState("");
  const [editId, setEditId] = useState(false);
  const [scope, setScope] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [byCode, setByCode] = useState(false);       // the sign-in button is offered first; the code is the fallback
  const ticks = useRef(0);

  const load = () => api<OAuthProvider[]>("/api/oauth/providers").then((list) => {
    const x = list.find((y) => y.id === id) ?? null;
    setP(x);
    setScope((cur) => cur || x?.scopes[0]?.id || "");
  }).catch(() => {});
  useEffect(() => { load(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps

  // follow the attempt with short requests; it goes on at the server even if this page is closed
  useEffect(() => {
    if (!flow || flow.status !== "pending") return;
    let live = true;
    ticks.current = 0;
    const t = setInterval(async () => {
      setNow(Date.now());
      if (++ticks.current % 3 !== 0) return;   // the clock ticks every second, the server is asked every third second
      try {
        const f = await api<OAuthFlow>(`/api/oauth/flows/${flow.id}`);
        if (!live) return;
        setFlow(f);
        if (f.status !== "pending") { load(); if (f.status === "connected") onChanged?.(); }
      } catch (e) {
        if (live && e instanceof ApiError && e.status === 404) setFlow(null);
      }
    }, 1000);
    return () => { live = false; clearInterval(t); };
  }, [flow?.id, flow?.status]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!p) return <p className="muted small">불러오는 중…</p>;

  async function saveClientId() {
    setErr(""); setBusy(true);
    try { setP(await api<OAuthProvider>(`/api/oauth/${id}/client`, { method: "PUT", json: { client_id: clientId.trim() } })); setClientId(""); setEditId(false); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function start() {
    setErr(""); setBusy(true);
    try {
      const f = await api<OAuthFlow>(`/api/oauth/${id}/start`, { method: "POST", json: { scope } });
      setFlow(f); setDeadline(Date.now() + f.expires_in * 1000); setNow(Date.now()); setCopied(false);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function cancel() {
    if (flow) await api(`/api/oauth/flows/${flow.id}`, { method: "DELETE" }).catch(() => {});
    setFlow(null);
  }
  async function disconnect() {
    if (!confirm(`${p!.name} 연결을 끊을까요? 이 앱에 저장된 토큰이 지워져요.\n${p!.name} 쪽 권한은 ${p!.name} 설정의 '승인된 앱'에서 따로 지울 수 있어요.`)) return;
    await api(`/api/credentials/${p!.item}`, { method: "DELETE" });
    setFlow(null); load(); onChanged?.();
  }
  async function copy() {
    try { await navigator.clipboard.writeText(flow!.user_code); setCopied(true); } catch { /* the code is on the screen to read */ }
  }

  // connected
  if (p.connected && (!flow || flow.status === "connected")) {
    return (
      <div className="form">
        <b><ShieldIcon /> {p.name} 연결됨</b>
        <span className="small muted">{p.account || p.name} · 토큰은 안전한 보관함에 암호화돼 있고, 에이전트에게는 보이지 않아요.</span>
        {onNotify && <button type="button" className="primary" onClick={() => onNotify(`${p.name}를 연결했어요. 이어서 진행해 줘.`)}>에이전트에게 알리기</button>}
        <button type="button" className="link danger" onClick={disconnect}>연결 끊기</button>
        <a className="small" href={p.settings_url} target="_blank" rel="noreferrer">{p.name}에서 권한 관리하기 ›</a>
      </div>
    );
  }

  // waiting for the code to be typed
  if (flow && flow.status === "pending") {
    const secs = Math.max(0, Math.round((deadline - now) / 1000));
    return (
      <div className="form">
        <b>{p.name}에서 이 코드를 입력해 주세요</b>
        <div className="device-code" aria-label="인증 코드">{flow.user_code}</div>
        <div className="row">
          <button type="button" onClick={copy}>{copied ? "복사했어요 ✓" : "코드 복사"}</button>
          <a className="button primary" href={flow.verification_uri} target="_blank" rel="noreferrer">{p.name} 열기</a>
        </div>
        <span className="small muted">{p.name} 페이지가 열리면 코드를 붙여 넣고 허용해 주세요. 끝나면 여기가 자동으로 바뀌어요. · 남은 시간 {mmss(secs)}</span>
        <button type="button" className="link" onClick={cancel}>취소</button>
      </div>
    );
  }

  async function signIn() {
    setErr(""); setBusy(true);
    try {
      const { url } = await api<{ url: string }>("/api/github/web/start", { method: "POST", json: { origin: location.origin, scope } });
      location.assign(url);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }

  const ended = flow && flow.status !== "pending" && flow.status !== "connected" ? flow : null;
  const scopePicker = (
    <>
      <label>어디까지 허용할까요?
        <select value={scope} onChange={(e) => setScope(e.target.value)}>{p.scopes.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select>
      </label>
      <span className="small muted">{p.scopes.find((s) => s.id === scope)?.detail}</span>
    </>
  );
  if (p.web && !byCode) {
    return (
      <div className="form">
        {ended && <span className="error small">{ended.message || "연결하지 못했어요."}</span>}
        {scopePicker}
        <button type="button" className="primary" disabled={busy} onClick={signIn}>{busy ? "여는 중…" : `${p.name}로 로그인`}</button>
        <span className="small muted">{p.name} 로그인 화면에서 아이디·비밀번호로 로그인하고 허용하면 이 앱으로 돌아와요. 비밀번호는 이 앱에 들어오지 않아요.</span>
        <button type="button" className="link" onClick={() => setByCode(true)}>코드로 연결하기</button>
        {err && <span className="error small">{err}</span>}
        {p.id === "github" && <OperatorSettings provider="github" />}
      </div>
    );
  }
  return (
    <div className="form">
      {ended && <span className="error small">{ended.message || "연결하지 못했어요."}</span>}
      {!p.has_client_id || editId ? (
        <>
          <b>{p.name} 앱 등록이 한 번 필요해요</b>
          <ol className="steps small">{p.help.map((h, i) => <li key={i}>{h}</li>)}</ol>
          <input placeholder="Client ID" value={clientId} onChange={(e) => setClientId(e.target.value)} autoCapitalize="none" spellCheck={false} autoComplete="off" />
          <button type="button" className="primary" disabled={busy || clientId.trim().length < 6} onClick={saveClientId}>저장</button>
          {editId && <button type="button" className="link" onClick={() => setEditId(false)}>취소</button>}
        </>
      ) : (
        <>
          {scopePicker}
          <button type="button" className="primary" disabled={busy} onClick={start}>{busy ? "준비 중…" : `${p.name} 연결하기`}</button>
          <button type="button" className="link" onClick={() => setEditId(true)}>Client ID 바꾸기</button>
          {p.web && <button type="button" className="link" onClick={() => setByCode(false)}>{p.name}로 로그인하기</button>}
        </>
      )}
      {err && <span className="error small">{err}</span>}
      {p.id === "github" && <OperatorSettings provider="github" />}
    </div>
  );
}

/** For the person who runs Kelo for others: the OAuth app (GitHub or Google) everyone signs in with. The secret can be replaced, never read back. */
export function OperatorSettings({ provider }: { provider: "github" | "google" }) {
  const title = provider === "github" ? "GitHub OAuth 앱 (운영자용)" : "Google OAuth 클라이언트 (운영자용)";
  const secretHint = provider === "github" ? "Client secret" : "Client secret (GOCSPX-…)";
  const [open, setOpen] = useState(false);
  const [app, setApp] = useState<{ client_id: string; has_secret: boolean; is_broker: boolean; callback: string } | null>(null);
  const [cid, setCid] = useState("");
  const [secret, setSecret] = useState("");
  const [msg, setMsg] = useState("");
  async function load() {
    const a = await api<{ client_id: string; has_secret: boolean; is_broker: boolean; callback: string }>(`/api/oauth-apps/${provider}`);
    setApp(a); setCid(a.client_id);
  }
  async function save() {
    setMsg("");
    try {
      await api(`/api/oauth-apps/${provider}`, { method: "PUT", json: { client_id: cid.trim(), ...(secret.trim() ? { client_secret: secret.trim() } : {}) } });
      setSecret(""); await load(); setMsg("저장했어요 ✓ 화면을 새로고침하면 로그인 버튼이 나와요.");
    } catch (e) { setMsg((e as Error).message); }
  }
  if (!open) return <button type="button" className="link" onClick={() => { setOpen(true); load().catch(() => {}); }}>운영자 설정</button>;
  if (!app) return <p className="muted small">불러오는 중…</p>;
  return (
    <div className="card form" style={{ marginTop: 6 }}>
      <b>{title}</b>
      <span className="small muted">모든 사용자가 이 앱으로 로그인해요. 비밀키는 이 서버에만 암호화돼 있고, 다른 사람들의 Kelo는 이 서버에 토큰 발급만 부탁해요.</span>
      {app.callback && <span className="small">{provider === "github" ? "Authorization callback URL" : "승인된 리디렉션 URI"}: <code>{app.callback}</code></span>}
      <input placeholder="Client ID" value={cid} onChange={(e) => setCid(e.target.value)} autoCapitalize="none" spellCheck={false} autoComplete="off" />
      <input type="password" placeholder={app.has_secret ? `${secretHint} 저장됨 · 바꾸려면 입력` : secretHint} value={secret}
             onChange={(e) => setSecret(e.target.value)} autoComplete="new-password" spellCheck={false} />
      <button type="button" className="primary" disabled={!cid.trim()} onClick={save}>저장</button>
      <span className="small muted">{app.is_broker ? "✓ 이 서버가 로그인을 중계하고 있어요." : "아직 중계하지 않아요. Client ID와 secret을 모두 넣으면 시작해요."}</span>
      {msg && <span className="small">{msg}</span>}
    </div>
  );
}

import { useEffect, useState } from "react";
import { api, type Connector, type KeyForm, type MailAccount, type MailProvider, type VaultInfo } from "./api";
import { DeviceConnect, OperatorSettings } from "./DeviceConnect";
import { ShieldIcon } from "./icons";
import { SecureCard } from "./Vault";

/** Sign in on the service's own page (hosted MCP servers). The service sends the user back to /oauth/callback, which returns to the app. */
function McpConnect({ c, onChanged, onNotify }: { c: Connector; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  async function go() {
    setErr(""); setBusy(true);
    try {
      const { url } = await api<{ url: string }>(`/api/mcp-oauth/${c.id}/start`, { method: "POST", json: { origin: location.origin } });
      location.assign(url);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }
  async function disconnect() {
    if (!confirm(`${c.name} 연결을 끊을까요? 이 앱에 저장된 로그인 정보가 지워져요.\n${c.name} 쪽 권한은 ${c.name} 설정에서 따로 지울 수 있어요.`)) return;
    await api(`/api/credentials/${c.item}`, { method: "DELETE" });
    onChanged?.();
  }
  if (c.status === "connected") {
    return (
      <div className="form">
        <b><ShieldIcon /> {c.name} 연결됨</b>
        <span className="small muted">로그인 정보는 안전한 보관함에 암호화돼 있고, 에이전트에게는 보이지 않아요. 무언가를 바꾸는 동작은 매번 허락을 받아요.</span>
        {onNotify && <button type="button" className="primary" onClick={() => onNotify(`${c.name}를 연결했어요. 이어서 진행해 줘.`)}>에이전트에게 알리기</button>}
        <button type="button" className="link danger" onClick={disconnect}>연결 끊기</button>
      </div>
    );
  }
  return (
    <div className="form">
      <b>{c.name} 연결</b>
      <span className="small muted">{c.name} 페이지가 열리면 로그인하고 허용해 주세요. 끝나면 이 앱으로 돌아와요. 비밀번호는 이 앱에 들어오지 않아요.</span>
      <button type="button" className="primary" disabled={busy} onClick={go}>{busy ? "여는 중…" : `${c.name}에서 로그인하기`}</button>
      {err && <span className="error small">{err}</span>}
    </div>
  );
}

/** Google Calendar: the Google sign-in first; the service account stays as the other way. */
function GoogleConnect({ c, onChanged, onNotify }: { c: Connector; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [st, setSt] = useState<{ available: boolean; reason: string; accounts: { name: string; address: string; can_write: boolean; problem: boolean }[] } | null>(null);
  const [err, setErr] = useState("");
  const [other, setOther] = useState(false);
  const load = () => api<typeof st>("/api/google/calendar").then(setSt).catch(() => {});
  useEffect(() => { load(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  async function go() {
    setErr("");
    try {
      const { url } = await api<{ url: string }>("/api/google/web/start", { method: "POST", json: { origin: location.origin, product: "calendar" } });
      location.assign(url);
    } catch (e) { setErr((e as Error).message); }
  }
  async function disconnect(name: string, address: string) {
    if (!confirm(`${address} 캘린더 연결을 끊을까요?`)) return;
    await api(`/api/credentials/${encodeURIComponent(name)}`, { method: "DELETE" });
    load(); onChanged?.();
  }
  if (!st) return <p className="muted small">불러오는 중…</p>;
  return (
    <div className="form">
      {st.accounts.map((a) => (
        <div key={a.name}><b><ShieldIcon /> {a.address}</b> <button type="button" className="link danger" onClick={() => disconnect(a.name, a.address)}>연결 끊기</button></div>
      ))}
      {st.accounts.length > 0 && onNotify && <button type="button" className="primary" onClick={() => onNotify("Google 캘린더를 연결했어요. 이어서 진행해 줘.")}>에이전트에게 알리기</button>}
      <span className="small muted">{c.description}</span>
      {st.available ? <button type="button" className={st.accounts.length ? "" : "primary"} onClick={go}>{st.accounts.length ? "다른 Google 계정 추가" : "Google로 로그인하기"}</button>
        : <span className="small muted">{st.reason}</span>}
      {err && <span className="error small">{err}</span>}
      {c.fallback && (other ? <KeyConnect c={{ ...c, connect: "key", via: c.fallback.via, item: c.fallback.item, description: c.fallback.blurb, status: c.fallback.connected ? "connected" : "available" }} onChanged={onChanged} />
        : <button type="button" className="link" onClick={() => setOther(true)}>서비스 계정으로 연결하기</button>)}
    </div>
  );
}

/** A mailbox with an app password. The server signs in once to check it; only a mailbox that lets it in is saved. */
function MailConnect({ c, onChanged, onNotify }: { c: Connector; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [providers, setProviders] = useState<MailProvider[]>([]);
  const [accounts, setAccounts] = useState<MailAccount[]>([]);
  const [provider, setProvider] = useState(c.id === "gmail" ? "gmail" : "naver");
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [adding, setAdding] = useState(false);
  const [googleWeb, setGoogleWeb] = useState(false);
  const [byPassword, setByPassword] = useState(false);
  const load = () => api<{ providers: MailProvider[]; accounts: MailAccount[]; google_web?: boolean }>("/api/mail/providers")
    .then((r) => { setProviders(r.providers); setAccounts(r.accounts); setGoogleWeb(!!r.google_web); }).catch(() => {});
  async function googleSignIn() {
    setErr(""); setBusy(true);
    try {
      const { url } = await api<{ url: string }>("/api/google/web/start", { method: "POST", json: { origin: location.origin } });
      location.assign(url);
    } catch (e) { setErr((e as Error).message); setBusy(false); }
  }
  useEffect(() => { load(); }, []);

  const shown = c.id === "gmail" ? accounts.filter((a) => a.provider === "gmail") : accounts;
  const choices = c.id === "gmail" ? providers.filter((p) => p.id === "gmail") : providers;
  const p = providers.find((x) => x.id === provider);

  async function save() {
    setErr(""); setBusy(true);
    try {
      await api("/api/mail/accounts", { method: "POST", json: { provider, address: address.trim(), password } });
      setPassword(""); setAddress(""); setAdding(false);
      await load(); onChanged?.();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function disconnect(a: MailAccount) {
    if (!confirm(`${a.label} 연결을 끊을까요? 저장된 앱 비밀번호가 지워져요.`)) return;
    await api(`/api/credentials/${a.name}`, { method: "DELETE" });
    await load(); onChanged?.();
  }

  return (
    <div className="form">
      {shown.map((a) => (
        <div key={a.name} className="form" style={{ gap: 4 }}>
          <b><ShieldIcon /> {a.label}</b>
          <span className="small muted">메일을 찾고 읽을 수 있어요. 보내기는 매번 받는 사람을 보여 주고 허락을 받아요.</span>
          <button type="button" className="link danger" onClick={() => disconnect(a)}>연결 끊기</button>
        </div>
      ))}
      {shown.length > 0 && onNotify && <button type="button" className="primary" onClick={() => onNotify("메일을 연결했어요. 이어서 진행해 줘.")}>에이전트에게 알리기</button>}
      {(shown.length === 0 || adding) && c.id === "gmail" && googleWeb && !byPassword ? (
        <>
          <button type="button" className="primary" disabled={busy} onClick={googleSignIn}>{busy ? "여는 중…" : "Google로 로그인"}</button>
          <span className="small muted">Google 로그인 화면에서 로그인하고 Gmail 권한을 허용하면 이 앱으로 돌아와요. 비밀번호는 이 앱에 들어오지 않아요.</span>
          <button type="button" className="link" onClick={() => setByPassword(true)}>앱 비밀번호로 연결하기</button>
        </>
      ) : (shown.length === 0 || adding) ? (
        <>
          {choices.length > 1 && (
            <label>메일
              <select value={provider} onChange={(e) => setProvider(e.target.value)}>{choices.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select>
            </label>
          )}
          {p && <ol className="steps small">{p.help.map((h, i) => <li key={i}>{h}</li>)}</ol>}
          <input placeholder="메일 주소" value={address} onChange={(e) => setAddress(e.target.value)} autoCapitalize="none" spellCheck={false} autoComplete="off" inputMode="email" />
          <input type="password" placeholder="앱 비밀번호" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" spellCheck={false} />
          <button type="button" className="primary" disabled={busy || !address.trim() || !password} onClick={save}>{busy ? "로그인해 보는 중…" : "확인하고 연결"}</button>
          <span className="small muted">먼저 실제로 로그인해 보고, 되는 경우에만 저장해요. 비밀번호는 암호화돼 보관되고 에이전트에게 보이지 않아요.</span>
          {adding && <button type="button" className="link" onClick={() => setAdding(false)}>취소</button>}
        </>
      ) : c.id !== "gmail" && <button type="button" className="link" onClick={() => setAdding(true)}>다른 메일 추가</button>}
      {err && <span className="error small">{err}</span>}
      {c.id === "gmail" && <OperatorSettings provider="google" />}
    </div>
  );
}

/** A key or token made on the service (or a Zoom app, or a Google service account). The server tries it once before saving it. */
function KeyConnect({ c, onChanged, onNotify }: { c: Connector; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [f, setF] = useState<KeyForm | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = () => api<KeyForm>(`/api/keys/${c.via}`).then(setF).catch(() => {});
  useEffect(() => { load(); }, [c.via]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!f) return <p className="muted small">불러오는 중…</p>;
  const google = f.id === "google";
  const account = f.label.split(" · ")[1] ?? "";

  async function save() {
    setErr(""); setBusy(true);
    try {
      await api(`/api/keys/${f!.id}`, { method: "POST", json: { values } });
      setValues({}); await load(); onChanged?.();
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function disconnect() {
    if (!confirm(`${f!.name} 연결을 끊을까요? 저장된 키가 지워져요.`)) return;
    await api(`/api/credentials/${f!.item}`, { method: "DELETE" });
    await load(); onChanged?.();
  }
  async function readFile(file: File | undefined) {
    if (file) setValues({ ...values, key: await file.text() });
  }

  if (f.connected) {
    return (
      <div className="form">
        <b><ShieldIcon /> {f.label} 연결됨</b>
        {google && account && (
          <span className="small">쓰려는 캘린더·파일·폴더를 이 주소와 공유해 주세요. 공유한 것만 볼 수 있어요.<br /><code>{account}</code></span>
        )}
        <span className="small muted">키는 안전한 보관함에 암호화돼 있고, 에이전트에게는 보이지 않아요. 무언가를 바꾸는 요청은 매번 허락을 받아요.</span>
        {onNotify && <button type="button" className="primary" onClick={() => onNotify(`${f.name}를 연결했어요. 이어서 진행해 줘.`)}>에이전트에게 알리기</button>}
        <button type="button" className="link danger" onClick={disconnect}>연결 끊기</button>
      </div>
    );
  }
  const ready = f.fields.every((x) => (values[x.id] ?? "").trim());
  return (
    <div className="form">
      <ol className="steps small">{f.help.map((h, i) => <li key={i}>{h}</li>)}</ol>
      {f.fields.map((x) => google && x.id === "key" ? (
        <div key={x.id} className="form" style={{ gap: 6 }}>
          <label className="upload-btn">JSON 파일 고르기<input type="file" accept=".json,application/json" hidden onChange={(e) => readFile(e.target.files?.[0])} /></label>
          <textarea rows={4} placeholder="또는 JSON 내용을 붙여 넣기" value={values.key ?? ""} spellCheck={false}
                    onChange={(e) => setValues({ ...values, key: e.target.value })} />
        </div>
      ) : (
        <input key={x.id} type={x.secret ? "password" : "text"} placeholder={x.placeholder || x.label} aria-label={x.label} value={values[x.id] ?? ""}
               autoComplete={x.secret ? "new-password" : "off"} autoCapitalize="none" spellCheck={false}
               onChange={(e) => setValues({ ...values, [x.id]: e.target.value })} />
      ))}
      <button type="button" className="primary" disabled={busy || !ready} onClick={save}>{busy ? "확인하는 중…" : f.checked ? "확인하고 연결" : "저장"}</button>
      <span className="small muted">{f.checked ? "먼저 실제로 한 번 불러 보고, 되는 경우에만 저장해요. " : "이 서비스는 확인 요청에도 요금이 붙어서, 처음 쓸 때 맞는지 알 수 있어요. "}
        값은 암호화돼 보관되고 에이전트에게 보이지 않아요.</span>
      {err && <span className="error small">{err}</span>}
    </div>
  );
}

/** A site the agent uses with its own browser. A login, if the site needs one, is kept in the vault and typed by the server. */
function BrowserConnect({ c, onNotify }: { c: Connector; onNotify?: (text: string) => void }) {
  const host = c.site ? new URL(c.site).hostname : "";
  const [saved, setSaved] = useState<string | null>(null);
  const [asking, setAsking] = useState<string | null>(null);
  const [err, setErr] = useState("");
  const load = () => api<VaultInfo>("/api/vault").then((v) => {
    const it = v.items.find((i) => i.kind === "login" && i.hosts.includes(host));
    setSaved(it ? (it.username_hint ?? "저장됨") : "");
  }).catch(() => setSaved(""));
  useEffect(() => { load(); }, [host]); // eslint-disable-line react-hooks/exhaustive-deps
  async function ask() {
    setErr("");
    try {
      const r = await api<{ name: string }>("/api/vault/logins", { method: "POST", json: { site: c.site, label: c.name } });
      setAsking(r.name);
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <div className="form">
      <a className="small" href={c.site} target="_blank" rel="noreferrer">{host} ›</a>
      {saved ? <span className="small"><ShieldIcon /> 로그인 정보 보관 중 · {saved}</span>
        : asking ? <SecureCard name={asking} onNotify={onNotify} />
        : saved === "" && <button type="button" onClick={ask}>로그인 정보 맡겨 두기 (선택)</button>}
      <span className="small muted">로그인 정보는 보관함에 암호화돼 있고, 서버가 그 사이트의 로그인 칸에만 입력해요. 에이전트는 볼 수 없어요.</span>
      {err && <span className="error small">{err}</span>}
    </div>
  );
}

/** The right way to connect this service, whichever it is. Without `connector`, it looks the service up by id (the chat card does that). */
export function ConnectPanel({ id, connector, onChanged, onNotify }: { id: string; connector?: Connector; onChanged?: () => void; onNotify?: (text: string) => void }) {
  const [c, setC] = useState<Connector | null>(connector ?? null);
  const reload = () => api<Connector[]>("/api/connectors").then((l) => setC(l.find((x) => x.id === id) ?? null)).catch(() => {});
  useEffect(() => { if (connector) setC(connector); else reload(); }, [id, connector]); // eslint-disable-line react-hooks/exhaustive-deps
  if (!c) return <p className="muted small">불러오는 중…</p>;
  const changed = () => { onChanged?.(); if (!connector) reload(); };
  if (c.connect === "device") return <DeviceConnect id={c.via || c.id} onChanged={changed} onNotify={onNotify} />;
  if (c.connect === "key") return <KeyConnect c={c} onChanged={changed} onNotify={onNotify} />;
  if (c.connect === "browser") return <BrowserConnect c={c} onNotify={onNotify} />;
  if (c.connect === "autofill") return <div className="form"><ol className="steps small">{(c.steps ?? []).map((x, i) => <li key={i}>{x}</li>)}</ol></div>;
  if (c.connect === "mcp") return <McpConnect c={c} onChanged={changed} onNotify={onNotify} />;
  if (c.connect === "mail") return <MailConnect c={c} onChanged={changed} onNotify={onNotify} />;
  if (c.connect === "google") return <GoogleConnect c={c} onChanged={changed} onNotify={onNotify} />;
  return <p className="muted small">{c.status === "unsupported" ? c.description : "아직 연결할 수 없어요."}</p>;
}

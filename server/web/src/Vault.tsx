import { useEffect, useState } from "react";
import { Group, Row, SectionLabel, SubPage } from "./Rows";
import { KeyIcon, PlusIcon, ShieldIcon, GlobeIcon } from "./icons";
import { api, type ProviderInfo, type VaultInfo, type VaultItem, type VaultLogRow } from "./api";

const PLACE: Record<string, string> = {
  bearer_header: "Authorization: Bearer", custom_header: "사용자 지정 헤더", query_param: "쿼리 파라미터", url_path_segment: "주소 경로",
};
const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "아직 없어요");
const ACTION: Record<string, string> = {
  used: "사용", denied: "거절", requested: "요청", added: "추가", saved: "저장", replaced: "바꿈", "entered-once": "입력",
  deleted: "삭제", setting: "설정", locked: "잠금", unlocked: "잠금 해제", result: "결과", failed: "실패", rejected: "키 거절", cancelled: "취소",
};
/** What the last real use showed, in words. `null` when it was never used. */
const RESULT: Record<string, string> = {
  ok: "마지막 사용 성공 ✓", failed: "마지막 로그인이 실패했어요", rejected: "서비스가 키를 거절했어요", code_needed: "사이트가 인증 코드를 요구했어요",
  blocked: "로봇 확인이 막고 있어요", unknown: "결과를 확실히 알 수 없었어요",
};
const BAD = new Set(["failed", "rejected"]);

/**
 * The form where a secret is typed. It is the only way in: the value goes to the server and is never shown again, and the assistant
 * never sees it. Used in the vault and on the card that appears in the chat when the assistant needs a login or a key.
 */
type Mode = "after_success" | "now" | "once";
const MODE_TEXT: Record<Mode, string> = { after_success: "로그인에 성공하면 저장 (권장)", now: "지금 바로 저장", once: "한 번만 쓰기" };

export function SecureEntry({ item, onSaved, defaultMode = "now" }: { item: VaultItem; onSaved: (mode: Mode) => void; defaultMode?: Mode }) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [mode, setMode] = useState<Mode>(defaultMode);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const login = item.kind === "login";

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setMsg("");
    try {
      if (login) await api(`/api/vault/${item.name}/login`, { method: "PUT", json: { username: user.trim(), password: pass, mode } });
      else await api(`/api/credentials/${item.name}/secret`, { method: "PUT", json: { secret: pass.trim() } });
      setUser(""); setPass("");
      onSaved(mode);
    } catch (x) { setMsg((x as Error).message); } finally { setBusy(false); }
  }
  return (
    <form className="form" onSubmit={submit} autoComplete={login ? "on" : "off"}>
      {login && <span className="small muted">폰에 1Password 같은 비밀번호 앱이 있으면, 칸을 눌러 <b>{item.hosts[0]}</b> 로그인을 골라 채울 수 있어요.</span>}
      {/* a login form the phone's password manager recognises and fills (with Face ID / fingerprint); a service key is never offered for autofill */}
      {login && <label>아이디<input name="username" autoComplete="username" spellCheck={false} autoCapitalize="none" value={user} onChange={(e) => setUser(e.target.value)} maxLength={200} /></label>}
      <label>{login ? "비밀번호" : "키"}<input name={login ? "password" : "vault-secret"} type="password" autoComplete={login ? "current-password" : "new-password"} spellCheck={false} value={pass} onChange={(e) => setPass(e.target.value)} maxLength={512} /></label>
      {login && (
        <label>저장 방식
          <select value={mode} onChange={(e) => setMode(e.target.value as Mode)}>
            {(Object.keys(MODE_TEXT) as Mode[]).map((m) => <option key={m} value={m}>{MODE_TEXT[m]}</option>)}
          </select>
          <span className="small muted">
            {mode === "after_success" ? "에이전트가 실제로 로그인해 보고, 사이트가 받아 줄 때만 저장해요. 실패하면 저장하지 않아요."
              : mode === "now" ? "입력한 그대로 저장해요. 아직 로그인해 보지 않았으니 틀렸는지는 처음 쓸 때 알 수 있어요."
              : "이번 한 번만 쓰고 지워요. 저장하지 않아요."}
          </span>
        </label>
      )}
      <button className="primary" disabled={busy || !pass || (login && !user.trim()) || (!login && pass.trim().length < 8)}>{busy ? "저장 중…" : "안전하게 보관"}</button>
      {msg && <span className="error small">{msg}</span>}
      <span className="small muted">입력한 값은 암호화돼 저장되고, 에이전트와 화면에 다시 보이지 않아요.</span>
    </form>
  );
}

/** The card that appears in the chat when the assistant asks for a login or a key. */
export function SecureCard({ name, onNotify }: { name: string; onNotify?: (text: string) => void }) {
  const [item, setItem] = useState<VaultItem | null | undefined>(undefined);
  const [done, setDone] = useState<Mode | null>(null);
  const [closed, setClosed] = useState(false);
  useEffect(() => { api<VaultInfo>("/api/vault").then((v) => setItem([...v.items, ...v.pending].find((i) => i.name === name) ?? null)).catch(() => setItem(null)); }, [name]);
  if (!item) return null;
  const what = item.kind === "login" ? `${item.label} 로그인 정보` : `${item.label} 키`;
  async function close() {   // nothing was entered: the request is forgotten, and nothing is left in the vault
    await api(`/api/vault/${name}/draft`, { method: "DELETE" }).catch(() => {});
    setClosed(true);
  }
  if (closed) return <div className="approval secure-card"><span className="small muted">{what} 입력을 취소했어요. 아무것도 저장하지 않았어요.</span></div>;
  return (
    <div className="approval secure-card">
      <strong><ShieldIcon /> 필요한 항목: {what}</strong>
      <span className="small muted">{item.hosts.join(", ")} 에서만 쓸 수 있도록 등록돼요. 여기에 입력하면 에이전트는 값을 볼 수 없어요.</span>
      {done ? (
        <>
          <span className="small">{done === "after_success" ? "받았어요 ✓ 로그인에 성공하면 보관함에 저장돼요." : done === "once" ? "받았어요 ✓ 한 번만 쓰고 지워요." : "저장했어요 ✓"}</span>
          {onNotify && <button type="button" className="primary" onClick={() => onNotify(`보관함에 ${done === "now" ? "저장" : "입력"}했어요: ${what}. 이어서 진행해 줘.`)}>에이전트에게 알리기</button>}
        </>
      ) : (
        <>
          <SecureEntry item={item} defaultMode={item.kind === "login" ? "after_success" : "now"} onSaved={setDone} />
          <button type="button" className="link" onClick={close}>취소</button>
        </>
      )}
    </div>
  );
}

function ItemPage({ item, locked, verifying, onChanged, onBack }: { item: VaultItem; locked: boolean; verifying: boolean; onChanged: () => void; onBack: () => void }) {
  const [msg, setMsg] = useState("");
  const bad = !!item.last_result && BAD.has(item.last_result);
  const signedIn = item.kind === "email" || item.placement?.via === "oauth" || item.name === "github-api";   // made in the Connectors tab, not by typing a key
  const [open, setOpen] = useState(!item.has_secret || bad);
  async function tryAgain() {
    try { await api(`/api/vault/${item.name}`, { method: "PATCH", json: { clear_result: true } }); onChanged(); } catch (e) { setMsg((e as Error).message); }
  }
  async function allow(v: boolean) {
    try { await api(`/api/vault/${item.name}`, { method: "PATCH", json: { auto_allow: v } }); onChanged(); } catch (e) { setMsg((e as Error).message); }
  }
  async function remove() {
    if (!confirm(`보관함에서 "${item.label}" 지울까요? 저장된 값도 함께 지워져요.`)) return;
    await api(`/api/credentials/${item.name}`, { method: "DELETE" });
    onChanged(); onBack();
  }
  return (
    <SubPage title={item.label} onBack={onBack}>
      <div className="card form">
        <span>{item.kind === "login" ? "웹사이트 로그인" : item.kind === "email" ? "이메일 계정" : signedIn ? "로그인으로 연결한 서비스" : "외부 서비스 키"} · <code>{item.hosts.join(", ")}</code></span>
        <span className="small muted">
          {item.has_secret ? `저장됨${bad ? "" : " ✓"}${item.username_hint ? ` · ${item.username_hint}` : ""}` : "아직 입력하지 않았어요"} · 마지막 사용 {when(item.last_used)}
          {item.kind === "api" && !signedIn && ` · 키 위치 ${item.placement.type === "custom_header" ? `헤더 ${item.placement.header}` : item.placement.type === "query_param" ? `쿼리 ${item.placement.param}` : PLACE[item.placement.type ?? ""] ?? ""}`}
          {item.endpoint && ` · MCP ${item.endpoint}`}
        </span>
        {locked && <span className="error small">보관함이 잠겨 있어서 에이전트가 쓸 수 없어요.</span>}
        {verifying && <span className="small">입력한 로그인은 에이전트가 실제로 로그인해 보고 성공하면 저장돼요.</span>}
        {item.last_result ? (
          <span className={bad ? "error small" : "small muted"}>
            {RESULT[item.last_result] ?? item.last_result} · {when(item.last_result_at)}{item.kind === "api" && item.last_result_detail ? ` · ${item.last_result_detail}` : ""}
          </span>
        ) : item.has_secret && <span className="small muted">아직 써 보지 않았어요. 틀렸는지는 처음 쓸 때 알 수 있어요.</span>}
        {bad && !signedIn && (
          <>
            <span className="error small">{item.kind === "login" ? "같은 틀린 비밀번호로 계속 시도하면 계정이 잠길 수 있어서, 에이전트는 다시 시도하지 않아요. 아래에서 다시 입력하거나, 맞다고 확신하면 다시 시도하게 할 수 있어요." : "키가 틀렸거나 만료됐을 수 있어요. 아래에서 다시 입력해 주세요."}</span>
            <button type="button" className="link" onClick={tryAgain}>그래도 다시 시도하게 하기</button>
          </>
        )}
      </div>
      {item.kind === "login" && (
        <div className="card form">
          <div className="section-head" style={{ marginTop: 0 }}>로그인할 때</div>
          <div className="segmented" style={{ marginBottom: 0 }}>
            <button className={!item.auto_allow ? "on" : ""} onClick={() => allow(false)}>매번 물어보기</button>
            <button className={item.auto_allow ? "on" : ""} onClick={() => allow(true)}>물어보지 않고</button>
          </div>
          <span className="small muted">
            {item.auto_allow ? "에이전트가 이 사이트의 로그인 페이지에서 바로 로그인해요. 이 설정은 여기서만 바꿀 수 있어요."
              : "에이전트가 로그인하려고 할 때마다 어느 사이트에 입력하는지 보여 주고 허락을 받아요."}
          </span>
        </div>
      )}
      <div className="card form">
        {signedIn ? <span className="small muted">{bad ? "연결이 끊겼어요. " : ""}다시 연결하거나 바꾸려면 커넥터 탭에서 해요.</span>
          : open ? <SecureEntry item={item} onSaved={() => { setOpen(false); onChanged(); }} /> : (
          <button type="button" onClick={() => setOpen(true)}>{item.kind === "login" ? "아이디·비밀번호 바꾸기" : "키 바꾸기"}</button>
        )}
      </div>
      <div className="card form"><button type="button" className="link danger" onClick={remove}>보관함에서 지우기</button></div>
      {msg && <span className="error small">{msg}</span>}
    </SubPage>
  );
}

function AddLogin({ onDone, onBack }: { onDone: (name: string) => void; onBack: () => void }) {
  const [site, setSite] = useState("");
  const [label, setLabel] = useState("");
  const [err, setErr] = useState("");
  async function add() {
    setErr("");
    try { const r = await api<{ name: string }>("/api/vault/logins", { method: "POST", json: { site: site.trim(), label: label.trim() } }); onDone(r.name); }
    catch (e) { setErr((e as Error).message); }
  }
  return (
    <SubPage title="웹사이트 로그인 추가" onBack={onBack}>
      <div className="card form">
        <label>로그인 페이지 주소<input placeholder="https://github.com/login" value={site} onChange={(e) => setSite(e.target.value)} autoCapitalize="none" spellCheck={false} /></label>
        <label>이름 (선택)<input placeholder="GitHub" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} /></label>
        <span className="small muted">이 사이트(https)에서만 쓸 수 있어요. 다음 화면에서 아이디와 비밀번호를 안전하게 입력해요.</span>
        {err && <span className="error small">{err}</span>}
        <button className="primary" disabled={!site.trim()} onClick={add}>다음</button>
      </div>
    </SubPage>
  );
}

function AddApi({ onDone, onBack }: { onDone: (name: string) => void; onBack: () => void }) {
  const [f, setF] = useState({ name: "", label: "", hosts: "", type: "bearer_header", header: "", param: "", mcp: "" });
  const [err, setErr] = useState("");
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });
  async function add() {
    setErr("");
    const placement: Record<string, string> = { type: f.type };
    if (f.type === "custom_header") placement.header = f.header.trim();
    if (f.type === "query_param") placement.param = f.param.trim();
    try {
      await api("/api/credentials", { method: "POST", json: { name: f.name.trim(), label: f.label.trim(), hosts: f.hosts.split(/[\s,]+/).filter(Boolean), placement, mcp_endpoint: f.mcp.trim() } });
      onDone(f.name.trim().toLowerCase().replace(/^custom\./, ""));
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <SubPage title="외부 서비스 키 추가" onBack={onBack}>
      <p className="muted small" style={{ margin: "0 4px" }}>
        이미지 만들기·음성처럼 에이전트가 쓸 외부 서비스(API)를 등록해요. 키는 아래에 적은 API 주소로만 보내지고, 키 입력은 다음 화면에서 해요.
      </p>
      <div className="card form">
        <input placeholder="이름 (예: acme)" value={f.name} onChange={set("name")} autoCapitalize="none" />
        <input placeholder="표시 이름" value={f.label} onChange={set("label")} />
        <input placeholder="API 주소 (예: api.acme.com, 여러 개는 쉼표)" value={f.hosts} onChange={set("hosts")} autoCapitalize="none" />
        <select value={f.type} onChange={set("type")}>{Object.entries(PLACE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        {f.type === "custom_header" && <input placeholder="헤더 이름 (예: X-API-Key)" value={f.header} onChange={set("header")} />}
        {f.type === "query_param" && <input placeholder="파라미터 이름 (예: api_key)" value={f.param} onChange={set("param")} />}
        <input placeholder="MCP 서버 주소 (선택, https://…)" value={f.mcp} onChange={set("mcp")} />
        {err && <span className="error small">{err}</span>}
        <button className="primary" disabled={!f.name.trim() || !f.hosts.trim()} onClick={add}>다음</button>
      </div>
    </SubPage>
  );
}

/** The key of an AI service (OpenAI and the like). Changing or removing it happens here; choosing which model answers is on the model page. */
function AiKeyPage({ p, onBack, onGoModel, onChanged }: { p: ProviderInfo; onBack: () => void; onGoModel: () => void; onChanged: () => void }) {
  const [key, setKey] = useState("");
  const [msg, setMsg] = useState("");
  async function save() {
    setMsg("");
    try { await api(`/api/providers/${p.name}/key`, { method: "PUT", json: { api_key: key.trim() } }); setKey(""); setMsg("저장했어요 ✓"); onChanged(); }
    catch (e) { setMsg((e as Error).message); }
  }
  async function remove() {
    if (!confirm(`${p.display_name} 키를 지울까요? 저장된 값이 지워지고, 이 서비스로는 답할 수 없게 돼요.`)) return;
    await api(`/api/providers/${p.name}/key`, { method: "DELETE" });
    onChanged(); onBack();
  }
  return (
    <SubPage title={p.display_name} onBack={onBack}>
      <div className="card form">
        <span>AI 모델을 쓰기 위한 API 키</span>
        <span className="small muted">앱에 저장됨 ✓{p.key_hint ? ` · ····${p.key_hint}` : ""}</span>
        <span className="small muted">이 서비스로 답하게 할 때만 쓰여요. 키 값은 에이전트에게도, 화면에도 보이지 않아요.</span>
      </div>
      <div className="card form">
        <label>새 키로 바꾸기<input type="password" autoComplete="new-password" spellCheck={false} placeholder="API 키 붙여넣기" value={key} onChange={(e) => setKey(e.target.value)} maxLength={512} /></label>
        <button type="button" className="primary" disabled={key.trim().length < 8} onClick={save}>저장</button>
        {msg && <span className="small muted">{msg}</span>}
      </div>
      <div className="card form"><button type="button" className="link danger" onClick={remove}>저장된 키 삭제</button></div>
      <p className="muted small" style={{ margin: "0 4px" }}>어떤 모델로 답할지는 <button type="button" className="link" onClick={onGoModel}>모델 설정</button>에서 골라요.</p>
    </SubPage>
  );
}

function LogPage({ onBack }: { onBack: () => void }) {
  const [rows, setRows] = useState<VaultLogRow[] | null>(null);
  useEffect(() => { api<VaultLogRow[]>("/api/vault/log?limit=200").then(setRows).catch(() => setRows([])); }, []);
  return (
    <SubPage title="사용 기록" onBack={onBack}>
      <p className="muted small" style={{ marginTop: 0 }}>보관함에서 일어난 일이에요. 에이전트가 쓴 것과 내가 한 것이 모두 남고, 에이전트는 이 기록을 읽거나 지울 수 없어요.</p>
      {rows && rows.length === 0 && <p className="muted small" style={{ textAlign: "center", marginTop: 24 }}>아직 기록이 없어요.</p>}
      {rows && rows.length > 0 && (
        <div className="menu-card">
          {rows.map((r, i) => (
            <div key={i} className="log-row">
              <span className={`badge${r.ok ? "" : " denied"}`}>{ACTION[r.action] ?? r.action}</span>
              <span className="grow"><b>{r.item}</b>{r.detail && <div className="small muted">{r.detail}</div>}</span>
              <span className="small muted">{when(r.ts)}</span>
            </div>
          ))}
        </div>
      )}
    </SubPage>
  );
}

type View = { v: "list" } | { v: "item"; name: string } | { v: "ai"; name: string } | { v: "addLogin" } | { v: "addApi" } | { v: "log" };

/** The secure vault: every password and key the assistant may use, what each is for, and the lock. Values can be added or replaced here, never read. */
export function VaultPage({ onBack, onGoModel }: { onBack: () => void; onGoModel: () => void }) {
  const [info, setInfo] = useState<VaultInfo | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [view, setView] = useState<View>({ v: "list" });
  const load = () => api<VaultInfo>("/api/vault").then(setInfo).catch(() => {});
  useEffect(() => { load(); api<ProviderInfo[]>("/api/providers").then(setProviders).catch(() => {}); }, []);

  async function toggleLock() {
    if (!info) return;
    await api("/api/vault/lock", { method: "PUT", json: { locked: !info.locked } });
    load();
  }
  const list = () => setView({ v: "list" });
  const loadProviders = () => api<ProviderInfo[]>("/api/providers").then(setProviders).catch(() => {});
  if (!info) return <SubPage title="안전한 보관함" onBack={onBack}><p className="muted small">불러오는 중…</p></SubPage>;

  if (view.v === "log") return <LogPage onBack={list} />;
  if (view.v === "addLogin") return <AddLogin onBack={list} onDone={(name) => { load(); setView({ v: "item", name }); }} />;
  if (view.v === "addApi") return <AddApi onBack={list} onDone={(name) => { load(); setView({ v: "item", name }); }} />;
  if (view.v === "ai") {
    const p = providers.find((x) => x.name === view.name);
    if (p) return <AiKeyPage p={p} onBack={list} onGoModel={onGoModel} onChanged={loadProviders} />;
  }
  if (view.v === "item") {
    const item = [...info.items, ...info.pending].find((i) => i.name === view.name);
    if (item) {
      // leaving a login or key that was started but never filled in: nothing stays behind in the vault
      const leave = () => {
        if (!item.has_secret) api(`/api/vault/${item.name}/draft`, { method: "DELETE" }).catch(() => {});
        list(); load();
      };
      return <ItemPage item={item} locked={info.locked} verifying={info.verifying.includes(item.name)} onChanged={load} onBack={leave} />;
    }
  }

  const logins = info.items.filter((i) => i.kind === "login");
  const mailboxes = info.items.filter((i) => i.kind === "email");
  const keys = info.items.filter((i) => i.kind === "api");
  const status = (i: VaultItem) => (   // every item listed here holds a value; requests without one are not shown
    i.last_result && BAD.has(i.last_result) ? <span className="error">⚠ {i.kind === "login" ? "로그인 실패" : "키 거절됨"}</span>
      : i.last_result === "ok" ? `✓ ${i.username_hint ?? "저장됨"}` : (i.username_hint ?? "저장됨"));
  const ai = providers.filter((p) => p.key_source === "app");
  return (
    <SubPage title="안전한 보관함" onBack={onBack}>
      <div className="card form">
        <div className="between"><b><ShieldIcon /> {info.locked ? "에이전트 사용이 잠겨 있어요" : "안전하게 보관 중이에요"}</b>
          <button type="button" className={info.locked ? "primary" : ""} onClick={toggleLock}>{info.locked ? "잠금 해제" : "에이전트 사용 잠그기"}</button></div>
        <span className="small muted">
          {info.locked ? "에이전트는 지금 보관함의 어떤 것도 쓸 수 없어요."
            : "비밀번호와 키는 암호화해 저장하고, 에이전트에게는 값이 보이지 않아요. 에이전트가 쓸 때마다 사용 기록에 남아요."}
        </span>
      </div>

      <SectionLabel>웹사이트 로그인</SectionLabel>
      {logins.length > 0 && <Group>{logins.map((i) => <Row key={i.name} icon={<GlobeIcon />} label={i.label} value={status(i)} onClick={() => setView({ v: "item", name: i.name })} />)}</Group>}
      <Group><Row icon={<PlusIcon />} label="웹사이트 로그인 추가" onClick={() => setView({ v: "addLogin" })} /></Group>

      {mailboxes.length > 0 && (
        <>
          <SectionLabel>이메일 계정</SectionLabel>
          <Group>{mailboxes.map((i) => <Row key={i.name} icon={<KeyIcon />} label={i.label.split(" · ")[0]} value={status(i)} onClick={() => setView({ v: "item", name: i.name })} />)}</Group>
        </>
      )}

      <SectionLabel>외부 서비스 키</SectionLabel>
      <p className="muted small" style={{ margin: "0 4px 8px" }}>커넥터 탭에서 로그인해 연결한 서비스와, 이미지 만들기·음성 같은 외부 서비스를 에이전트가 쓸 때 필요한 키예요. 에이전트가 그 서비스를 부를 때 앱이 키를 대신 붙여 보내고, 에이전트는 키를 볼 수 없어요.</p>
      {keys.length > 0 && <Group>{keys.map((i) => <Row key={i.name} icon={<KeyIcon />} label={i.label} value={status(i)} onClick={() => setView({ v: "item", name: i.name })} />)}</Group>}
      <Group><Row icon={<PlusIcon />} label="외부 서비스 키 추가" onClick={() => setView({ v: "addApi" })} /></Group>

      {ai.length > 0 && (
        <>
          <SectionLabel>AI 모델 키</SectionLabel>
          <Group>{ai.map((p) => <Row key={p.name} icon={<KeyIcon />} label={p.display_name} value={p.key_hint ? `····${p.key_hint}` : "저장됨"} onClick={() => setView({ v: "ai", name: p.name })} />)}</Group>
        </>
      )}

      <SectionLabel>기록</SectionLabel>
      <Group><Row icon={<ShieldIcon />} label="사용 기록" onClick={() => setView({ v: "log" })} /></Group>
    </SubPage>
  );
}

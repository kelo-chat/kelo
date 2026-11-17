import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ApiError, answerApproval, api, talk, uploadDocument, type Profile, type ProviderInfo, type StoredMessage, type StreamEvent } from "./api";
import { SendIcon, StopIcon } from "./icons";
import { Md, splitBubbles } from "./Md";
import { ModelSheet } from "./ModelSheet";
import { ConnectPanel } from "./ConnectPanel";
import { SecureCard } from "./Vault";

type Item =
  | { kind: "msg"; role: "user" | "assistant"; text: string; key: string }
  | { kind: "tool"; id: string; name: string; state: "running" | "ok" | "error" | "denied"; service?: string; key: string }
  | { kind: "approval"; id: string; name: string; args: string; detail?: string; answered?: boolean; key: string }
  | { kind: "vault"; name: string; key: string }
  | { kind: "connect"; service: string; key: string }
  | { kind: "notice"; text: string; key: string }
  | { kind: "error"; text: string; key: string };

const EFFORT_LABEL: Record<string, string> = { "": "기본", low: "낮음", medium: "보통", high: "높음", xhigh: "매우 높음", max: "최대" };

/** What the status line says while a reply is in progress; "since" drives the elapsed seconds. */
type Phase = { kind: "waiting" | "thinking" | "writing" | "tool" | "approval" | "switching"; label?: string; since: number };

const PHASE_TEXT: Record<Phase["kind"], string> = {
  waiting: "응답 대기 중", thinking: "생각 중", writing: "응답 중", tool: "도구 실행 중",
  approval: "승인 대기 중", switching: "대체 모델로 전환 중",
};

let seq = 0;
const k = () => `k${++seq}`;

export function Chat({ profile, onProfile }: { profile: Profile; onProfile: (p: Profile) => void }) {
  const agentName = profile.agent_name;
  const [sheet, setSheet] = useState(false);
  const [effortMenu, setEffortMenu] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [phase, setPhase] = useState<Phase | null>(null);   // what the model is doing right now (status line)
  const [now, setNow] = useState(Date.now());
  const [items, setItems] = useState<Item[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => { api<ProviderInfo[]>("/api/providers").then(setProviders).catch(() => {}); }, []);
  const current = providers.find((p) => p.name === profile.provider);
  const levels = current?.effort_levels ?? [];

  async function pickEffort(value: string) {
    setEffortMenu(false);
    onProfile(await api<Profile>("/api/profile", { method: "PUT", json: { effort: value } }));
  }

  // Another phone may have talked to the same assistant: keep this screen in step while it is idle and in view.
  const lastId = useRef(0);
  const reload = () => api<StoredMessage[]>("/api/messages?limit=100").then((ms) => {
    lastId.current = ms.length ? ms[ms.length - 1].id : 0;
    setItems(ms.map((m) => ({ kind: "msg", role: m.role, text: m.content, key: `m${m.id}` })));
  });
  const remember = () => api<StoredMessage[]>("/api/messages?limit=1").then((ms) => { lastId.current = ms.length ? ms[ms.length - 1].id : 0; });
  useEffect(() => { reload().catch(() => {}); }, []);
  useEffect(() => {
    if (busy) return;
    const check = () => {
      if (document.hidden) return;
      api<StoredMessage[]>("/api/messages?limit=1")
        .then((ms) => { if ((ms.length ? ms[ms.length - 1].id : 0) !== lastId.current) return reload(); })
        .catch(() => {});
    };
    const t = setInterval(check, 4000);
    document.addEventListener("visibilitychange", check);
    return () => { clearInterval(t); document.removeEventListener("visibilitychange", check); };
  }, [busy]);

  useLayoutEffect(() => { endRef.current?.scrollIntoView({ block: "end" }); }, [items, phase?.kind]);
  useEffect(() => {
    if (!phase) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [phase === null]); // eslint-disable-line react-hooks/exhaustive-deps

  function applyPhase(ev: StreamEvent) {
    const to = (kind: Phase["kind"], label?: string) =>
      setPhase((p) => (p && p.kind === kind && p.label === label ? p : { kind, label, since: Date.now() }));
    switch (ev.type) {
      case "thinking": to("thinking"); break;
      case "text": to("writing"); break;
      case "tool_started": to("tool", ev.name); break;
      case "tool_result": to("waiting"); break;
      case "approval_request": to("approval", ev.name); break;
      case "fallback": to("switching", ev.model); break;
      case "retry": to("thinking"); break;
    }
  }

  function apply(ev: StreamEvent) {
    applyPhase(ev);
    setItems((prev) => {
      const next = [...prev];
      switch (ev.type) {
        case "text": {
          const last = next[next.length - 1];
          if (last?.kind === "msg" && last.role === "assistant") next[next.length - 1] = { ...last, text: last.text + ev.text };
          else next.push({ kind: "msg", role: "assistant", text: ev.text, key: k() });
          break;
        }
        case "tool_started":
          next.push({ kind: "tool", id: ev.id, name: ev.name, state: "running", service: typeof ev.arguments.service === "string" ? ev.arguments.service : undefined, key: k() });
          break;
        case "tool_result": {
          // the newest chip with this id that is still running (some CLIs reuse ids from one step to the next)
          let i = -1;
          for (let j = next.length - 1; j >= 0; j--) {
            const x = next[j];
            if (x.kind === "tool" && x.id === ev.id && x.state === "running") { i = j; break; }
          }
          if (i >= 0) next[i] = { ...(next[i] as Extract<Item, { kind: "tool" }>), state: ev.approved === false ? "denied" : (ev.is_error ? "error" : "ok") };
          // the assistant asked the user to connect a service (GitHub): the card with the code to type appears right here
          if (ev.name === "connect_service" && !ev.is_error && !ev.output.includes("already connected")) {
            const service = i >= 0 ? (next[i] as Extract<Item, { kind: "tool" }>).service : undefined;
            if (service) next.push({ kind: "connect", service, key: k() });
          }
          // the assistant asked for a login or a key: show the secure form right here (the value goes to the vault, not into the chat)
          if ((ev.name === "request_login" || ev.name === "request_api_access") && ev.approved && !ev.is_error) {
            const m = /'([a-z0-9][a-z0-9._-]{1,62}[a-z0-9])'/.exec(ev.output);
            if (m) next.push({ kind: "vault", name: m[1], key: k() });
          }
          break;
        }
        case "approval_request":
          next.push({ kind: "approval", id: ev.approval_id, name: ev.name, args: JSON.stringify(ev.arguments, null, 2), detail: ev.detail, key: k() });
          break;
        case "fallback":
          next.push({ kind: "notice", text: `↪ 대체 모델(${ev.model})로 전환했어요 · ${ev.reason}`, key: k() });
          break;
        case "retry":
          next.push({ kind: "notice", text: "↪ 모델이 파일만 둘러보고 멈춰서, 한 번 더 시도하고 있어요", key: k() });
          break;
        case "reconnecting":
          next.push({ kind: "notice", text: "📶 연결이 불안정해요. 다시 연결하는 중이에요 (답은 서버에서 계속 만들고 있어요)", key: k() });
          break;
        case "step_limit":
          next.push({ kind: "error", text: "단계 한도에 도달해서 멈췄어요.", key: k() });
          break;
        case "error":
          next.push({ kind: "error", text: ev.message, key: k() });
          break;
      }
      return next;
    });
  }

  async function send(given?: string) {
    const text = (given ?? input).trim();
    if (!text || busy) return;
    if (given === undefined) setInput("");
    setBusy(true);
    setPhase({ kind: "waiting", since: Date.now() });
    setItems((p) => [...p, { kind: "msg", role: "user", text, key: k() }]);
    const ctl = new AbortController();
    abort.current = ctl;
    try {
      await talk(text, apply, ctl.signal);
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        // a TypeError is the browser saying the line to the server broke (screen off, weak signal, tunnel hiccup); the server stopped that reply
        const message = e instanceof ApiError
          ? (e.status === 400 ? `${e.message} → 설정 탭에서 모델을 고르세요.` : e.message)
          : e instanceof TypeError ? "연결이 끊겼어요 (network error). 화면이 꺼졌거나 신호가 잠깐 불안정했을 수 있어요. 한 번 더 보내 주세요." : (e as Error).message;
        apply({ type: "error", message });
      }
    } finally {
      abort.current = null;
      remember().catch(() => {});   // this phone's own messages are already on screen: do not reload them
      setBusy(false);
      setPhase(null);
    }
  }

  async function attach(file: File | undefined) {
    if (!file) return;
    try {
      const path = await uploadDocument(file);
      setItems((p) => [...p, { kind: "notice", text: `📎 올렸어요: ${path.replace("uploads/", "")}. 에이전트에게 읽어 달라고 말해 보세요.`, key: k() }]);
      setInput((v) => v || `${path.replace("uploads/", "")} 내용을 읽고 요약해줘.`);
    } catch (e) {
      apply({ type: "error", message: (e as Error).message });
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function approve(id: string, ok: boolean) {
    setItems((p) => p.map((x) => (x.kind === "approval" && x.id === id ? { ...x, answered: true } : x)));
    setPhase({ kind: "waiting", since: Date.now() });
    try { await answerApproval(id, ok); } catch { /* expired: the stream reports the denial */ }
  }

  return (
    <div className="chat">
      <div className="scroll">
        {items.length === 0 && <p className="muted hint">{agentName}에게 무엇이든 말해 보세요.</p>}
        {items.map((it) => {
          switch (it.kind) {
            case "msg":
              return it.role === "user"
                ? <div key={it.key} className="bubble user">{it.text}</div>
                : <>{splitBubbles(it.text).map((part, n) => <div key={`${it.key}-${n}`} className="bubble md"><Md text={part} /></div>)}</>;
            case "tool":
              return <div key={it.key} className={`chip ${it.state}`}>{{ running: "⏳", ok: "✓", error: "✗", denied: "⛔" }[it.state]} {it.name}</div>;
            case "approval":
              return (
                <div key={it.key} className="approval">
                  <strong>{it.name}</strong> 실행을 허용할까요?
                  {it.detail && <div className="small" style={{ margin: "6px 0" }}>{it.detail}</div>}
                  <pre>{it.args}</pre>
                  {!it.answered && (
                    <div className="row">
                      <button onClick={() => approve(it.id, false)}>거절</button>
                      <button className="primary" onClick={() => approve(it.id, true)}>허용</button>
                    </div>
                  )}
                </div>
              );
            case "vault":
              return <SecureCard key={it.key} name={it.name} onNotify={(t) => send(t)} />;
            case "connect":
              return <div key={it.key} className="approval secure-card"><ConnectPanel id={it.service} onNotify={(t) => send(t)} /></div>;
            case "notice":
              return <div key={it.key} className="chip">{it.text}</div>;
            case "error":
              return <div key={it.key} className="bubble error-bubble">{it.text}</div>;
          }
        })}
        {phase && (
          <div className="status" role="status" aria-live="polite">
            <span className="dots" aria-hidden="true"><i /><i /><i /></span>
            {PHASE_TEXT[phase.kind]}{phase.label ? ` · ${phase.label}` : ""}…
            <span className="muted"> {Math.max(0, Math.floor((now - phase.since) / 1000))}초</span>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div className="chips">
        <button type="button" className="model-chip" onClick={() => setSheet(true)} aria-label="모델 설정">
          {profile.model ? profile.model : "모델 선택"} ▾
        </button>
        {profile.provider && providers.length > 0 && (  /* wait for the provider list: no "unsupported" flash while loading */
          <div className="menu-wrap">
            <button type="button" className="model-chip" disabled={levels.length === 0} aria-label="생각 수준"
                    title={levels.length === 0 ? (current?.effort_note ?? "이 제공자는 생각 수준을 지원하지 않아요") : undefined}
                    onClick={() => setEffortMenu((v) => !v)}>
              {levels.length === 0 ? (current?.effort_note ? "생각 수준: 모델에서 선택" : "생각 수준 미지원") : `생각 ${EFFORT_LABEL[profile.effort] ?? profile.effort}`}{levels.length > 0 && " ▾"}
            </button>
            {effortMenu && (
              <>
                <div className="menu-backdrop" onClick={() => setEffortMenu(false)} />
                <div className="menu" role="listbox" aria-label="생각 수준">
                  {["", ...levels].map((v) => (
                    <button key={v || "default"} type="button" role="option" aria-selected={profile.effort === v}
                            className={profile.effort === v ? "on" : ""} onClick={() => pickEffort(v)}>
                      {EFFORT_LABEL[v] ?? v}
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>
        )}
      </div>
      {sheet && <ModelSheet profile={profile} onSaved={onProfile} onClose={() => setSheet(false)} />}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <input ref={fileInput} type="file" hidden accept=".pdf,.docx,.xlsx,.xlsm" onChange={(e) => attach(e.target.files?.[0])} />
        <button type="button" className="round ghost" aria-label="문서 올리기" title="PDF, Word, Excel 올리기" onClick={() => fileInput.current?.click()}>＋</button>
        <textarea rows={1} placeholder="메시지 보내기" value={input} onChange={(e) => setInput(e.target.value)}
                  onKeyDown={(e) => {
                    // Enter sends; Shift+Enter = newline; never send mid-IME-composition (Korean).
                    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); }
                  }} />
        {busy
          ? <button type="button" className="round" onClick={() => abort.current?.abort()} aria-label="중지"><StopIcon /></button>
          : <button className="round" disabled={!input.trim()} aria-label="보내기"><SendIcon /></button>}
      </form>
    </div>
  );
}

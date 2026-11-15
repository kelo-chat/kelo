import { useEffect, useRef, useState } from "react";
import { ModelPicker } from "./ModelPicker";
import { SubPage } from "./Rows";
import { api, type ModelList, type Profile, type ProviderInfo } from "./api";

const CUSTOM = "__custom__";

/** Which AI service and model answer, the fallback model, and the API key for the service. */
export function ModelPage({ profile, onProfile, onBack }: { profile: Profile; onProfile: (p: Profile) => void; onBack: () => void }) {
  // (a fallback on the main model's own provider, from before the rule that they differ, is shown as empty)
  const sameFb = !!profile.fallback_provider && profile.fallback_provider === profile.provider;
  const [form, setForm] = useState({ provider: profile.provider, model: profile.model,
    fallback_provider: sameFb ? "" : profile.fallback_provider, fallback_model: sameFb ? "" : profile.fallback_model });
  const [fbNote, setFbNote] = useState("");
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");
  const [models, setModels] = useState<ModelList | null>(null);
  const [loadingModels, setLoadingModels] = useState(false);
  const [custom, setCustom] = useState(false);
  const [keyInput, setKeyInput] = useState("");
  const [keyMsg, setKeyMsg] = useState("");
  const [installing, setInstalling] = useState(false);
  const [installMsg, setInstallMsg] = useState("");
  const asked = useRef(new Set<string>());

  const loadProviders = () => api<ProviderInfo[]>("/api/providers").then(setProviders);
  useEffect(() => { loadProviders().catch(() => {}); }, []);

  const loadModels = (provider: string, refresh = false) => {
    if (!provider) { setModels(null); return; }
    setLoadingModels(true);
    api<ModelList>(`/api/models?provider=${encodeURIComponent(provider)}${refresh ? "&refresh=true" : ""}`)
      .then((m) => { setModels(m); setCustom(m.models.length === 0 || (!!form.model && !m.models.includes(form.model))); })
      .catch(() => setModels({ provider, models: [], error: "모델 목록을 불러오지 못했어요" }))
      .finally(() => setLoadingModels(false));
  };
  useEffect(() => { loadModels(form.provider); }, [form.provider]); // eslint-disable-line react-hooks/exhaustive-deps

  const current = providers.find((p) => p.name === form.provider);
  const set = (k: "provider" | "model") => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => { setSaved(false); setForm({ ...form, [k]: e.target.value }); };
  /** The main model and the fallback never come from the same provider: moving the main model onto the fallback's provider empties the fallback. */
  const setProvider = (e: React.ChangeEvent<HTMLSelectElement>) => {
    const provider = e.target.value;
    setSaved(false);
    if (provider && provider === form.fallback_provider) {
      setForm({ ...form, provider, fallback_provider: "", fallback_model: "" });
      setFbNote("기본 모델과 같은 제공자라서 대체 모델을 비웠어요.");
    } else {
      setForm({ ...form, provider });
      setFbNote("");
    }
  };

  async function install(name: string) {
    setInstalling(true);
    setInstallMsg("");
    try {
      await api(`/api/providers/${name}/install`, { method: "POST" });
      setInstallMsg("설치했어요");
    } catch (e) {
      setInstallMsg((e as Error).message);
    } finally {
      setInstalling(false);
      loadProviders();
    }
  }

  // Selecting a CLI provider that can be installed but is not: offer to install it (once per selection).
  useEffect(() => {
    const p = providers.find((x) => x.name === form.provider);
    if (p?.cli && !p.cli.installed && p.cli.installable && !asked.current.has(p.name)) {
      asked.current.add(p.name);
      if (confirm(`${p.display_name} CLI가 서버에 설치돼 있지 않아요.\n\n지금 설치할까요?\n\nGoogle의 설치 스크립트(antigravity.google/cli/install.sh)를 내려받아 서버 사용자 권한으로 실행하고, ~/.local/bin에 설치해요. 스크립트는 마지막에 \`agy install\`도 실행하는데, 이 단계가 셸 설정을 바꿀 수 있어요.`)) {
        install(p.name);
      }
    }
  }, [form.provider, providers]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveKey() {
    setKeyMsg("");
    try {
      await api(`/api/providers/${form.provider}/key`, { method: "PUT", json: { api_key: keyInput.trim() } });
      setKeyInput("");
      setKeyMsg("저장했어요");
      await loadProviders();
      loadModels(form.provider, true);
    } catch (e) {
      setKeyMsg((e as Error).message);
    }
  }
  async function removeKey() {
    if (!confirm("저장된 API 키를 삭제할까요?")) return;
    await api(`/api/providers/${form.provider}/key`, { method: "DELETE" });
    setKeyMsg("");
    await loadProviders();
    loadModels(form.provider, true);
  }

  async function save() {
    setErr("");
    try {
      onProfile(await api<Profile>("/api/profile", { method: "PUT", json: form }));
      setSaved(true);
    } catch (e) { setErr((e as Error).message); }
  }

  return (
    <SubPage title="모델" onBack={onBack}>
      <div className="card form">
        <label>제공자
          <select value={form.provider} onChange={setProvider}>
            <option value="">서버 기본값</option>
            {providers.map((p) => (
              <option key={p.name} value={p.name}>
                {p.display_name}{p.configured === true ? " ✓" : p.configured === false ? " (설정 필요)" : ""}
              </option>
            ))}
          </select>
        </label>
        {current?.cli && (
          <div className="keybox">
            <span className="small muted">
              {installing ? "설치 중… (최대 몇 분)"
                : !current.cli.installed ? (current.cli.remote ? "CLI 컨테이너에 연결할 수 없어요" : "CLI가 설치돼 있지 않아요")
                : current.cli.logged_in === false ? "CLI는 설치됐지만 로그인이 필요해요"
                : current.cli.logged_in ? `✓ CLI ${current.cli.remote ? "컨테이너 실행 중" : "설치됨"} · 로그인됨` : "✓ CLI 설치됨 · 로그인 상태는 확인할 수 없어요"}
            </span>
            {!current.cli.installed && current.cli.installable && !installing && (
              <button type="button" onClick={() => install(current.name)}>설치</button>
            )}
            {(!current.cli.installed || current.cli.logged_in !== true) && !installing && <span className="small muted">{current.cli.hint}</span>}
            {current.cli.installed && (current.cli.version || current.cli.update_hint) && (
              <span className="small muted">
                {current.cli.version && <>버전 {current.cli.version}</>}{current.cli.version && current.cli.update_hint && " · "}{current.cli.update_hint}
              </span>
            )}
            {installMsg && <span className="small muted" style={{ whiteSpace: "pre-wrap" }}>{installMsg}</span>}
          </div>
        )}
        {current?.takes_key && (
          <div className="keybox">
            <span className="small muted">
              API 키 {current.key_source === "app" ? `· 앱에 저장됨 (····${current.key_hint})`
                : current.key_source === "env" ? "· 서버 .env 사용 중" : "· 아직 없어요"}
            </span>
            <div className="row-inputs">
              <input type="password" autoComplete="off" spellCheck={false} placeholder={current.key_source ? "새 키로 바꾸기" : "API 키 붙여넣기"}
                     value={keyInput} onChange={(e) => setKeyInput(e.target.value)} />
              <button type="button" className="primary" disabled={keyInput.trim().length < 8} onClick={saveKey}>저장</button>
            </div>
            {current.key_source === "app" && <button type="button" className="link danger" onClick={removeKey}>저장된 키 삭제</button>}
            {keyMsg && <span className="small muted">{keyMsg}</span>}
          </div>
        )}
        {current?.oauth && !current.logged_in && (
          <p className="error small">로그인이 필요해요: 서버에서 <code>python -m assistant.providers.oauth login {current.name}</code></p>
        )}
        {form.provider && (
          <label>
            <span className="between">모델
              <button type="button" className="link" onClick={() => loadModels(form.provider, true)} disabled={loadingModels}>
                {loadingModels ? "불러오는 중…" : "새로고침"}
              </button>
            </span>
            {models && models.models.length > 0 && (
              <select value={custom ? CUSTOM : form.model}
                      onChange={(e) => { setSaved(false); if (e.target.value === CUSTOM) { setCustom(true); } else { setCustom(false); setForm({ ...form, model: e.target.value }); } }}>
                {!form.model && <option value="">모델 선택…</option>}
                {models.models.map((m) => <option key={m} value={m}>{models.labels?.[m] ? `${models.labels[m]} · ${m}` : m}</option>)}
                <option value={CUSTOM}>직접 입력…</option>
              </select>
            )}
            {(custom || !models || models.models.length === 0) && (
              <input value={form.model} onChange={set("model")} placeholder="모델 이름 직접 입력" />
            )}
            {models?.error && <span className="error small">{models.error}</span>}
          </label>
        )}
        {!form.provider && <p className="muted small">제공자를 고르지 않으면 서버 기본값(<code>.env</code>)을 써요.</p>}
        <div className="section-head">대체 모델</div>
        <p className="muted small" style={{ margin: 0 }}>위 모델이 답하기 전에 실패하면(한도 초과, 서버 오류 등) 자동으로 이 모델로 다시 시도해요.</p>
        <ModelPicker providers={providers} provider={form.fallback_provider} model={form.fallback_model} noneLabel="사용 안 함" exclude={form.provider}
                     onChange={(v) => { setSaved(false); setFbNote(""); setForm({ ...form, fallback_provider: v.provider, fallback_model: v.model }); }} />
        {fbNote && <p className="muted small" style={{ margin: 0 }}>{fbNote}</p>}
        <button className="primary" onClick={save}>{saved ? "저장됨 ✓" : "저장"}</button>
        {err && <span className="error small">{err}</span>}
      </div>
    </SubPage>
  );
}

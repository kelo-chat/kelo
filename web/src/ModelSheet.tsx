import { useEffect, useState } from "react";
import { api, type ProviderInfo, type Profile } from "./api";
import { ModelPicker } from "./ModelPicker";

/** Bottom sheet opened from the chat screen: pick the model and an automatic fallback. */
export function ModelSheet({ profile, onSaved, onClose }: { profile: Profile; onSaved: (p: Profile) => void; onClose: () => void }) {
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [main, setMain] = useState({ provider: profile.provider, model: profile.model });
  // (a fallback on the main model's own provider, from before this rule, is shown as empty)
  const [fb, setFb] = useState(profile.fallback_provider && profile.fallback_provider === profile.provider
    ? { provider: "", model: "" } : { provider: profile.fallback_provider, model: profile.fallback_model });
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState("");

  useEffect(() => { api<ProviderInfo[]>("/api/providers").then(setProviders); }, []);

  const mainOk = !main.provider || !!main.model.trim();
  const fbOk = !fb.provider || !!fb.model.trim();

  /** The main model and the fallback never come from the same provider: moving the main model onto the fallback's provider empties the fallback. */
  function changeMain(v: { provider: string; model: string }) {
    setMain(v);
    if (v.provider && v.provider === fb.provider) {
      setFb({ provider: "", model: "" });
      setNote("기본 모델과 같은 제공자라서 대체 모델을 비웠어요.");
    } else {
      setNote("");
    }
  }

  async function apply() {
    setBusy(true);
    setErr("");
    try {
      onSaved(await api<Profile>("/api/profile", {
        method: "PUT",
        json: { provider: main.provider, model: main.model.trim(), fallback_provider: fb.provider, fallback_model: fb.model.trim() },
      }));
      onClose();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="sheet-backdrop" onClick={onClose}>
      <div className="sheet" role="dialog" aria-label="모델 설정" onClick={(e) => e.stopPropagation()}>
        <div className="grabber" />
        <h3>모델</h3>
        <ModelPicker providers={providers} provider={main.provider} model={main.model} onChange={changeMain} noneLabel="서버 기본값" />
        <h3>대체 모델 <span className="small muted">· 위 모델이 답하기 전에 실패하면 자동으로 전환해요</span></h3>
        <ModelPicker providers={providers} provider={fb.provider} model={fb.model} onChange={(v) => { setNote(""); setFb(v); }} noneLabel="사용 안 함" exclude={main.provider} />
        {note && <p className="small muted" style={{ margin: "6px 0 0" }}>{note}</p>}
        {err && <p className="small error" style={{ margin: "6px 0 0" }}>{err}</p>}
        <div className="row" style={{ marginTop: 14 }}>
          <button onClick={onClose}>취소</button>
          <button className="primary" disabled={busy || !mainOk || !fbOk} onClick={apply}>적용</button>
        </div>
      </div>
    </div>
  );
}

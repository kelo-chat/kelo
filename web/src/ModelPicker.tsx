import { useEffect, useState } from "react";
import { api, type ModelList, type ProviderInfo } from "./api";

const CUSTOM = "__custom__";

type Props = {
  providers: ProviderInfo[];
  provider: string;
  model: string;
  onChange: (v: { provider: string; model: string }) => void;
  /** Offer an empty choice (used for the optional fallback model). */
  noneLabel?: string;
  /** A provider that cannot be chosen here: the fallback must come from another provider than the main model. */
  exclude?: string;
};

/** Provider + model in one block: the model list is whatever that provider really offers. */
export function ModelPicker({ providers, provider, model, onChange, noneLabel, exclude }: Props) {
  const [list, setList] = useState<ModelList | null>(null);
  const [loading, setLoading] = useState(false);
  const [custom, setCustom] = useState(false);
  const info = providers.find((p) => p.name === provider);

  useEffect(() => {
    if (!provider) { setList(null); return; }
    let live = true;
    setLoading(true);
    api<ModelList>(`/api/models?provider=${encodeURIComponent(provider)}`)
      .then((m) => { if (live) { setList(m); setCustom(m.models.length === 0 || (!!model && !m.models.includes(model))); } })
      .catch(() => live && setList({ provider, models: [], error: "모델 목록을 불러오지 못했어요" }))
      .finally(() => live && setLoading(false));
    return () => { live = false; };
  }, [provider]); // eslint-disable-line react-hooks/exhaustive-deps

  const mark = (p: ProviderInfo) => (p.configured === true ? " ✓" : p.configured === false ? " (설정 필요)" : "");
  const showSelect = list && list.models.length > 0;

  return (
    <div className="picker">
      <select value={provider} onChange={(e) => onChange({ provider: e.target.value, model: "" })} aria-label="제공자">
        <option value="">{noneLabel ?? "제공자 선택…"}</option>
        {providers.map((p) => {
          const taken = !!exclude && p.name === exclude;
          return <option key={p.name} value={p.name} disabled={taken}>{p.display_name}{mark(p)}{taken ? " · 기본 모델로 쓰는 중" : ""}</option>;
        })}
      </select>
      {provider && info?.configured === false && (
        <span className="small error">아직 준비되지 않았어요. 설정 탭에서 API 키나 로그인을 먼저 해 주세요.</span>
      )}
      {provider && (
        <>
          {showSelect && (
            <select value={custom ? CUSTOM : model} aria-label="모델"
                    onChange={(e) => e.target.value === CUSTOM ? setCustom(true) : (setCustom(false), onChange({ provider, model: e.target.value }))}>
              {!model && <option value="">{loading ? "불러오는 중…" : "모델 선택…"}</option>}
              {list!.models.map((m) => <option key={m} value={m}>{list!.labels?.[m] ? `${list!.labels[m]} · ${m}` : m}</option>)}
              <option value={CUSTOM}>직접 입력…</option>
            </select>
          )}
          {(custom || !showSelect) && (
            <input value={model} placeholder="모델 이름 직접 입력" onChange={(e) => onChange({ provider, model: e.target.value })} />
          )}
          {list?.error && <span className="small muted">{list.error}</span>}
        </>
      )}
    </div>
  );
}

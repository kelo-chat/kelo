import { SubPage } from "./Rows";
import type { Usage } from "./api";
import { fmt, UsageGroups, usageGroups } from "./usage";

const PROVIDER_NAME: Record<string, string> = { "antigravity-oauth": "Antigravity (agy)", "claude-subscription": "Claude" };

/** All the limits of the chosen model, or (when it reports none) the tokens this app has used. */
export function UsagePage({ usage, model, onRefresh, onBack }: { usage: Usage | null; model: string; onRefresh: () => void; onBack: () => void }) {
  return (
    <SubPage title="사용량 한도" onBack={onBack}>
      <div className="card">
        {!usage ? <p className="muted small">불러오는 중…</p> : usage.subscription ? (
          <>
            <div className="section-head" style={{ marginTop: 0 }}>
              {PROVIDER_NAME[usage.subscription.provider] ?? usage.subscription.provider}
              <button type="button" className="link" onClick={onRefresh}>새로고침</button>
            </div>
            {usage.subscription.error ? <p className="muted small">{usage.subscription.error}</p> : <UsageGroups groups={usageGroups(usage, model)} full />}
          </>
        ) : (
          <>
            <p className="muted small" style={{ marginTop: 0 }}>선택한 모델 제공자는 한도 정보를 주지 않아요. 이 앱에서 쓴 토큰이에요.</p>
            <div className="stat"><span>오늘</span><b>{fmt(usage.today.input_tokens + usage.today.output_tokens)}</b></div>
            <div className="stat"><span>누적</span><b>{fmt(usage.total.input_tokens + usage.total.output_tokens)}</b></div>
          </>
        )}
      </div>
    </SubPage>
  );
}

import { useState } from "react";
import { api, type Profile } from "./api";

const SUGGESTIONS = ["Pip", "Sage", "Nova", "Kite", "Juno"];

export function Onboarding({ initial, onDone }: { initial: Profile; onDone: (p: Profile) => void }) {
  const [step, setStep] = useState(initial.user_name ? 1 : 0);
  const [userName, setUserName] = useState(initial.user_name);
  const [agentName, setAgentName] = useState(initial.agent_name || SUGGESTIONS[0]);
  const [busy, setBusy] = useState(false);

  async function finish() {
    setBusy(true);
    try {
      onDone(await api<Profile>("/api/profile", { method: "PUT", json: { user_name: userName.trim(), agent_name: agentName.trim() } }));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="center-card">
      {step === 0 ? (
        <>
          <h1>안녕하세요!</h1>
          <p>저는 단순한 어시스턴트가 아니라 당신의 개인 에이전트예요. 시작하기 전에, 이름이 어떻게 되세요?</p>
          <input placeholder="이름" value={userName} onChange={(e) => setUserName(e.target.value)} autoFocus maxLength={60}
                 onKeyDown={(e) => e.key === "Enter" && !e.nativeEvent.isComposing && userName.trim() && setStep(1)} />
          <button className="primary" disabled={!userName.trim()} onClick={() => setStep(1)}>다음</button>
        </>
      ) : (
        <>
          <h1>{userName}님, 반가워요</h1>
          <p>저를 뭐라고 부르고 싶으세요? 나중에 언제든지 바꿀 수 있어요.</p>
          <div className="choices">
            {SUGGESTIONS.map((n) => (
              <button key={n} className={`choice ${agentName === n ? "on" : ""}`} onClick={() => setAgentName(n)}>{n}</button>
            ))}
          </div>
          <input placeholder="직접 입력" value={SUGGESTIONS.includes(agentName) ? "" : agentName}
                 onChange={(e) => setAgentName(e.target.value)} maxLength={60} />
          <button className="primary" disabled={busy || !agentName.trim()} onClick={finish}>시작</button>
        </>
      )}
    </div>
  );
}

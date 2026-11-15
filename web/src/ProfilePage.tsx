import { useState } from "react";
import { SubPage } from "./Rows";
import { api, type Profile } from "./api";

/** My name and the assistant's name. */
export function ProfilePage({ profile, onProfile, onBack }: { profile: Profile; onProfile: (p: Profile) => void; onBack: () => void }) {
  const [user, setUser] = useState(profile.user_name);
  const [agent, setAgent] = useState(profile.agent_name);
  const [saved, setSaved] = useState(false);
  const [err, setErr] = useState("");

  async function save() {
    setErr("");
    try {
      onProfile(await api<Profile>("/api/profile", { method: "PUT", json: { user_name: user.trim(), agent_name: agent.trim() } }));
      setSaved(true);
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <SubPage title="내 계정" onBack={onBack}>
      <div className="card form">
        <label>내 이름<input value={user} onChange={(e) => { setSaved(false); setUser(e.target.value); }} maxLength={60} /></label>
        <label>에이전트 이름<input value={agent} onChange={(e) => { setSaved(false); setAgent(e.target.value); }} maxLength={60} /></label>
        <button className="primary" disabled={!agent.trim()} onClick={save}>{saved ? "저장됨 ✓" : "저장"}</button>
        {err && <span className="error small">{err}</span>}
      </div>
    </SubPage>
  );
}

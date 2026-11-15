import { useState } from "react";
import { ApiError, api, setToken } from "./api";

export function Login({ onDone, notice }: { onDone: () => void; notice?: string }) {
  const [token, setTok] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [withKey, setWithKey] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setErr("");
    setToken(token.trim());
    try {
      await api("/api/profile");
      onDone();
    } catch (x) {
      setErr(x instanceof ApiError && x.status === 401 ? "접속 키가 맞지 않아요." : "서버에 연결하지 못했어요.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="center-card" onSubmit={submit}>
      <h1>시작하기</h1>
      {notice && <p className="error">{notice}</p>}
      <p className="muted">이 기기를 Kelo에 연결해요.</p>
      <ol className="steps">
        <li>이미 Kelo를 쓰는 폰이나 컴퓨터에서 <b>마이 → 내 기기 → 새 기기 연결</b>을 눌러요.</li>
        <li>나온 QR을 이 기기의 카메라로 찍거나, 함께 나온 링크를 이 기기에서 열어요.</li>
      </ol>
      <p className="muted small">처음 쓰는 거라 다른 기기가 없다면, 서버를 설치한 컴퓨터에서 <code>assistant phone add</code>를 실행하면 QR이 나와요.</p>
      {withKey ? (
        <>
          <input type="password" autoComplete="current-password" placeholder="접속 키" value={token}
                 onChange={(e) => setTok(e.target.value)} autoFocus />
          <span className="small muted">설치할 때 만든 접속 키(.env의 ASSISTANT_TOKEN)예요. 이 기기에만 저장돼요.</span>
          {err && <p className="error">{err}</p>}
          <button className="primary" disabled={busy || token.trim().length < 1}>{busy ? "확인 중…" : "연결"}</button>
        </>
      ) : <button type="button" className="link" onClick={() => setWithKey(true)}>접속 키로 연결하기</button>}
    </form>
  );
}

import { useEffect, useState } from "react";
import { api, clearToken, type Device, type PairStart } from "./api";

const ago = (t: number | null) => {
  if (!t) return "아직 사용 안 함";
  const s = Math.max(0, Date.now() / 1000 - t);
  return s < 120 ? "방금" : s < 7200 ? `${Math.floor(s / 60)}분 전` : s < 172800 ? `${Math.floor(s / 3600)}시간 전` : `${Math.floor(s / 86400)}일 전`;
};

/** Every phone or computer that can open this assistant. Add one with a QR code; remove one without touching the others. */
export function Devices({ onLogout }: { onLogout: () => void }) {
  const [devices, setDevices] = useState<Device[]>([]);
  const [current, setCurrent] = useState<number | null>(null);
  const [pair, setPair] = useState<(PairStart & { until: number }) | null>(null);
  const [left, setLeft] = useState(0);
  const [err, setErr] = useState("");
  const [copied, setCopied] = useState(false);

  const load = () => api<{ devices: Device[]; current: number | null }>("/api/devices")
    .then((d) => { setDevices(d.devices); setCurrent(d.current); }).catch(() => {});
  useEffect(() => { load(); }, []);
  useEffect(() => {
    if (!pair) return;
    const t = setInterval(() => {
      const s = Math.max(0, Math.round((pair.until - Date.now()) / 1000));
      setLeft(s);
      if (s === 0) setPair(null);
      else load();   // the new device shows up in the list as soon as it joins
    }, 2000);
    return () => clearInterval(t);
  }, [pair]);

  async function add() {
    setErr(""); setCopied(false);
    try {
      const p = await api<PairStart>("/api/devices/pair", { method: "POST", json: { origin: location.origin } });
      setPair({ ...p, until: Date.now() + p.expires_in * 1000 });
      setLeft(p.expires_in);
    } catch (e) { setErr((e as Error).message); }
  }
  async function remove(d: Device) {
    if (!confirm(d.id === current ? "이 기기의 연결을 끊을까요? 다시 쓰려면 새로 연결해야 해요." : `"${d.name}" 연결을 끊을까요?`)) return;
    await api(`/api/devices/${d.id}`, { method: "DELETE" });
    if (d.id === current) { clearToken(); onLogout(); } else load();
  }

  const insecure = location.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(location.hostname);
  return (
    <div className="card form hide-head">
      <div className="section-head" style={{ marginTop: 0 }}>내 기기</div>
      <p className="muted small" style={{ margin: 0 }}>
        기기는 몇 대든 연결할 수 있고, 같은 대화와 설정을 함께 써요. 기기마다 따로 끊을 수 있어서, 폰을 잃어버려도 나머지는 그대로예요.
      </p>
      {devices.length === 0 && <p className="muted small">아직 이 방식으로 연결한 기기가 없어요.</p>}
      {devices.map((d) => (
        <div key={d.id} className="keybox">
          <span><strong>{d.name}</strong>{d.id === current ? " · 이 기기" : ""}</span>
          <span className="small muted">연결 {new Date(d.created_at * 1000).toLocaleDateString()} · 마지막 사용 {ago(d.last_seen)}</span>
          <button type="button" className="link danger" onClick={() => remove(d)}>연결 끊기</button>
        </div>
      ))}
      {pair ? (
        <div className="keybox">
          <img alt="기기 연결 QR 코드" width={220} height={220} style={{ background: "#fff", borderRadius: 8, alignSelf: "center" }}
               src={`data:image/svg+xml;utf8,${encodeURIComponent(pair.qr_svg)}`} />
          <span className="small muted">새 폰의 카메라로 스캔하세요. 한 번만 쓸 수 있고 {Math.floor(left / 60)}분 {left % 60}초 뒤 사라져요.</span>
          <div className="row-inputs">
            <button type="button" onClick={() => { navigator.clipboard?.writeText(pair.link); setCopied(true); }}>{copied ? "복사됨 ✓" : "링크 복사"}</button>
            <button type="button" onClick={() => setPair(null)}>닫기</button>
          </div>
        </div>
      ) : (
        <button type="button" onClick={add}>기기 추가 (QR)</button>
      )}
      {insecure && <p className="error small">지금 주소는 https가 아니라서 새 기기에서 앱으로 설치되지 않을 수 있어요. Tailscale이나 Cloudflare로 연결한 주소에서 만드세요.</p>}
      {err && <span className="error small">{err}</span>}
    </div>
  );
}

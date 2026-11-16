import { useEffect, useState } from "react";
import { api } from "./api";

type Sizes = { memory: number; cpus: number; disk: number };
type Res = {
  memory: { used: number | null; limit: number | null }; cpus: number | null;
  disk: { used: number; limit: number | null; parts: Record<string, number>; free_on_host: number | null };
  host: { memory: number | null; cpus: number | null; disk_free: number | null; disk_total: number | null };
  profile: "recommended" | "minimum" | "unlimited" | "custom"; minimum: Sizes; recommended: Sizes;
};
const PROFILE: Record<Res["profile"], string> = { recommended: "권장 설정", minimum: "최소 설정", unlimited: "서버 전체 사용", custom: "직접 설정" };

const PART: Record<string, string> = { database: "대화 기록", uploads: "업로드한 문서", podcasts: "팟캐스트", images: "이미지", notes: "메모", history: "백업", other: "기타" };
const size = (n: number) => (n >= 1024 ** 3 ? `${(n / 1024 ** 3).toFixed(1)}GB` : n >= 1024 ** 2 ? `${(n / 1024 ** 2).toFixed(n >= 10 * 1024 ** 2 ? 0 : 1)}MB` : `${Math.max(0, Math.round(n / 1024))}KB`);

function Bar({ used, of }: { used: number; of: number | null }) {
  if (!of) return null;
  const pct = Math.min(100, (used / of) * 100);
  return <div className="bar" aria-hidden="true"><div style={{ width: `${pct}%`, height: "100%", borderRadius: 99, background: pct > 90 ? "var(--danger)" : "var(--accent)" }} /></div>;
}

/**
 * What this assistant may use on its computer: RAM, CPUs, storage. Where nothing was set, the ceiling is the computer itself,
 * so that is what is shown (never just "no limit"). Limits are set on the computer with `assistant limits`.
 */
export function Resources() {
  const [r, setR] = useState<Res | null>(null);
  useEffect(() => {
    const load = () => { if (!document.hidden) api<Res>("/api/resources").then(setR).catch(() => {}); };
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);
  if (!r) return null;
  const { memory, disk, host } = r;

  // the real ceiling of each: the limit that was set, else what the computer has
  const ramCeiling = memory.limit ?? host.memory;
  const cpuCeiling = r.cpus ?? host.cpus;
  const diskCeiling = disk.limit ?? (host.disk_free != null ? disk.used + host.disk_free : null);
  const full = disk.limit != null && disk.used >= disk.limit * 0.9;

  const hint = (min: number, rec: number, fmt: (n: number) => string, limit: number | null) => (
    <span className={limit != null && limit < min ? "error small" : "small muted"}>
      {limit != null && limit < min ? "최소보다 작아요 · " : ""}최소 {fmt(min)} · 권장 {fmt(rec)}
    </span>
  );
  return (
    <div className="card form">
      <div className="section-head" style={{ marginTop: 0 }}>{PROFILE[r.profile]}</div>
      <div className="keybox">
        <span>
          메모리(RAM) {memory.used != null ? `${size(memory.used)} 사용` : ""}
          {ramCeiling != null ? ` · ${memory.limit ? "한도" : "서버 전체"} ${size(ramCeiling)}` : ""}
        </span>
        {memory.used != null && <Bar used={memory.used} of={ramCeiling} />}
        {hint(r.minimum.memory, r.recommended.memory, size, memory.limit)}
      </div>
      <div className="keybox">
        <span>계산 능력(CPU) {cpuCeiling != null ? (r.cpus ? `최대 ${cpuCeiling}개` : `서버의 ${cpuCeiling}개 전부`) : "서버 전체"}</span>
        {hint(r.minimum.cpus, r.recommended.cpus, (n) => `${n}개`, r.cpus)}
      </div>
      <div className="keybox">
        <span>
          저장 공간 {size(disk.used)} 사용
          {diskCeiling != null ? ` · ${disk.limit ? "한도" : "서버 여유 공간 포함 최대"} ${size(diskCeiling)}` : ""}
        </span>
        <Bar used={disk.used} of={diskCeiling} />
        {hint(r.minimum.disk, r.recommended.disk, size, disk.limit)}
        <span className="small muted">
          {Object.entries(disk.parts).filter(([, n]) => n > 0).map(([k, n]) => `${PART[k] ?? k} ${size(n)}`).join(" · ") || "비어 있어요"}
        </span>
        {full && <span className="error small">저장 공간이 거의 찼어요. 업로드·팟캐스트를 지우면 공간이 생겨요. 가득 차면 새 파일을 저장하지 못해요(대화는 계속돼요).</span>}
      </div>
      <p className="muted small" style={{ margin: 0 }}>
        Kelo 서버: RAM {host.memory != null ? size(host.memory) : "?"} · CPU {host.cpus ?? "?"}개 · 디스크 여유 {host.disk_free != null ? size(host.disk_free) : "?"}.
        {" "}따로 정하지 않은 항목은 서버가 허락하는 만큼 써요. 한도는 서버를 설치한 컴퓨터에서 <code>assistant limits --choose</code>로 바꿔요.
      </p>
    </div>
  );
}

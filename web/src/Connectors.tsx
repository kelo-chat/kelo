import { useEffect, useState } from "react";
import { api, type Connector } from "./api";
import { ConnectPanel } from "./ConnectPanel";

const LABEL = { connected: "연결됨", disabled: "꺼짐", coming_soon: "준비 중", available: "연결하기", browser: "브라우저로 사용", autofill: "폰 앱으로 사용", unsupported: "지원 안 함" } as const;

export function Connectors() {
  const [list, setList] = useState<Connector[]>([]);
  const [q, setQ] = useState("");
  const [tab, setTab] = useState<"all" | "connected">("all");
  const [open, setOpen] = useState<Connector | null>(null);

  const reload = () => api<Connector[]>("/api/connectors").then((l) => { setList(l); setOpen((o) => (o ? l.find((c) => c.id === o.id) ?? null : o)); });
  useEffect(() => { reload(); }, []);

  const shown = list.filter((c) =>
    (tab === "all" || c.status === "connected") && `${c.name} ${c.description}`.toLowerCase().includes(q.toLowerCase()));
  const groups = [...new Set(shown.map((c) => c.category))];

  if (open) {
    return (
      <div className="page">
        <div className="between"><h2 style={{ margin: "8px 0" }}>{open.name}</h2><button type="button" className="link" onClick={() => setOpen(null)}>목록</button></div>
        <span className={`badge ${open.status}`}>{LABEL[open.status]}</span>
        <p style={{ marginBottom: 6 }}>{open.description}</p>
        {open.connect && <div className="card"><ConnectPanel id={open.id} connector={open} onChanged={reload} /></div>}
        {open.status === "coming_soon" && <p className="muted small">아직 연결할 수 없어요. 준비되면 여기서 연결할 수 있어요.</p>}
        {open.status === "unsupported" && <p className="muted small">이 앱에서는 쓸 수 없어요.</p>}
      </div>
    );
  }

  return (
    <div className="page">
      <h2>커넥터</h2>
      <input className="search" placeholder="검색" value={q} onChange={(e) => setQ(e.target.value)} />
      <div className="segmented">
        <button className={tab === "all" ? "on" : ""} onClick={() => setTab("all")}>사용 가능</button>
        <button className={tab === "connected" ? "on" : ""} onClick={() => setTab("connected")}>연결됨</button>
      </div>
      {groups.map((g) => (
        <section key={g}>
          <div className="section-head">{g}</div>
          <ul className="list card">
            {shown.filter((c) => c.category === g).map((c) => (
              <li key={c.id} style={{ cursor: "pointer" }} onClick={() => setOpen(c)}>
                <div className="grow"><div className="title">{c.name}</div><div className="muted small">{c.description}</div></div>
                <span className={`badge ${c.status}`}>{LABEL[c.status]}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
      {shown.length === 0 && <p className="muted">표시할 커넥터가 없어요.</p>}
    </div>
  );
}

import { useEffect, useState } from "react";
import { api, type Goal } from "./api";
import { TrashIcon } from "./icons";

const CATEGORIES = ["건강", "관계", "금융", "진로", "관심사"];

export function Goals() {
  const [goals, setGoals] = useState<Goal[]>([]);
  const [title, setTitle] = useState("");
  const [note, setNote] = useState("");
  const [category, setCategory] = useState("");
  const [open, setOpen] = useState(false);

  const load = () => api<Goal[]>("/api/goals").then(setGoals);
  useEffect(() => { load(); }, []);

  async function add() {
    if (!title.trim()) return;
    await api("/api/goals", { method: "POST", json: { title, note, category } });
    setTitle(""); setNote(""); setCategory(""); setOpen(false);
    load();
  }
  const toggle = (g: Goal) => api(`/api/goals/${g.id}`, { method: "PATCH", json: { status: g.status === "done" ? "tracking" : "done" } }).then(load);
  const remove = (g: Goal) => confirm(`"${g.title}" 목표를 삭제할까요?`) && api(`/api/goals/${g.id}`, { method: "DELETE" }).then(load);

  const tracking = goals.filter((g) => g.status === "tracking");
  const done = goals.filter((g) => g.status === "done");

  const row = (g: Goal) => (
    <li key={g.id} className={g.status}>
      <input type="checkbox" checked={g.status === "done"} onChange={() => toggle(g)} aria-label="완료" />
      <div className="grow">
        <div className="title">{g.title}</div>
        {(g.category || g.note) && <div className="muted small">{[g.category, g.note].filter(Boolean).join(" · ")}</div>}
      </div>
      <button className="icon" onClick={() => remove(g)} aria-label="삭제"><TrashIcon /></button>
    </li>
  );

  return (
    <div className="page">
      <h2>목표</h2>
      <section>
        <div className="section-head"><span className="dot" /> 추적 <button className="icon" onClick={() => setOpen(!open)} aria-label="목표 추가">{open ? "−" : "+"}</button></div>
        {open && (
          <div className="card form">
            <input placeholder="목표 (예: 매일 30분 운동)" value={title} onChange={(e) => setTitle(e.target.value)} maxLength={200} autoFocus />
            <input placeholder="메모 (선택)" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
            <div className="choices">
              {CATEGORIES.map((c) => (
                <button key={c} type="button" className={`choice ${category === c ? "on" : ""}`} onClick={() => setCategory(category === c ? "" : c)}>{c}</button>
              ))}
            </div>
            <button className="primary" disabled={!title.trim()} onClick={add}>추가</button>
          </div>
        )}
        <ul className="list">{tracking.map(row)}</ul>
        {tracking.length === 0 && !open && <p className="muted">추적 중인 목표가 없어요. + 를 눌러 만들어 보세요. 목표는 에이전트에게도 전달돼요.</p>}
      </section>
      {done.length > 0 && (
        <section>
          <div className="section-head">완료</div>
          <ul className="list">{done.map(row)}</ul>
        </section>
      )}
    </div>
  );
}

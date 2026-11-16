import { useEffect, useState } from "react";
import { Md } from "./Md";
import { Group, Row, SubPage } from "./Rows";
import { FileIcon } from "./icons";
import { api, downloadUpload, uploadDocument, type Upload } from "./api";

const kb = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(n / 1024))}KB`);
const day = (t: number) => new Date(t * 1000).toLocaleDateString("ko-KR", { month: "numeric", day: "numeric" });
const nameOf = (u: Upload) => u.path.replace("uploads/", "");

/** One document the user uploaded: its text, as the assistant reads it. */
function Viewer({ doc, onClose, onDeleted }: { doc: Upload; onClose: () => void; onDeleted: () => void }) {
  const name = nameOf(doc);
  const [text, setText] = useState<string | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    setText(null); setErr("");
    api<{ text: string }>(`/api/uploads/${encodeURIComponent(name)}/text`).then((d) => setText(d.text)).catch((e) => setErr((e as Error).message));
  }, [name]);

  async function remove() {
    if (!confirm("이 문서와 읽어 둔 텍스트를 지울까요?")) return;
    await api(`/api/uploads/${encodeURIComponent(name)}`, { method: "DELETE" });
    onDeleted();
  }
  return (
    <SubPage title={name} onBack={onClose}>
      <div className="card">
        <div className="between" style={{ marginBottom: 10 }}>
          <button type="button" className="link" onClick={() => downloadUpload(name).catch((e) => setErr((e as Error).message))}>원본 받기</button>
          <button type="button" className="link danger" onClick={remove}>삭제</button>
        </div>
        {err ? <p className="error small">{err}</p> : text === null ? <p className="muted small">문서를 읽는 중… (큰 문서는 몇 초 걸려요)</p>
          : <div className="doc"><Md text={text} headings /></div>}
      </div>
    </SubPage>
  );
}

/** The documents the user has uploaded, and only those: nothing the assistant came with is listed here. */
export function DocumentsPage({ onBack }: { onBack: () => void }) {
  const [docs, setDocs] = useState<Upload[] | null>(null);
  const [open, setOpen] = useState<Upload | null>(null);
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const load = () => api<Upload[]>("/api/uploads").then(setDocs).catch(() => setDocs([]));
  useEffect(() => { load(); }, []);

  async function onUpload(f: File | undefined, input: HTMLInputElement) {
    if (!f) return;
    setBusy(true); setMsg("");
    try { await uploadDocument(f); await load(); setMsg("올렸어요"); } catch (e) { setMsg((e as Error).message); } finally { setBusy(false); input.value = ""; }
  }

  if (open) return <Viewer doc={open} onClose={() => setOpen(null)} onDeleted={() => { setOpen(null); load(); }} />;
  return (
    <SubPage title="내 문서" onBack={onBack}>
      <div className="card form">
        <label className={`upload-btn${busy ? " busy" : ""}`}>
          {busy ? "올리는 중…" : "+ 문서 올리기"}
          <input type="file" hidden accept=".pdf,.docx,.xlsx,.xlsm" disabled={busy} onChange={(e) => onUpload(e.target.files?.[0], e.target)} />
        </label>
        <span className="small muted">PDF, Word, Excel · 25MB까지. 올린 문서는 에이전트가 읽을 수 있어요.</span>
        {msg && <span className="small muted">{msg}</span>}
      </div>
      {docs && docs.length === 0 && <p className="muted small" style={{ textAlign: "center", marginTop: 24 }}>아직 올린 문서가 없어요.</p>}
      {docs && docs.length > 0 && (
        <Group>
          {docs.map((d) => <Row key={d.path} icon={<FileIcon />} label={nameOf(d)} value={`${kb(d.bytes)} · ${day(d.modified)}`} onClick={() => setOpen(d)} />)}
        </Group>
      )}
    </SubPage>
  );
}

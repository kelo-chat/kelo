import { useEffect, useRef, useState } from "react";
import { api, getToken } from "./api";

type Episode = { slug: string; title: string; description: string; speakers: string[]; duration_secs: number | null; created_at: number };

const mmss = (s: number | null) => (s == null ? "" : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, "0")}`);

/** Episodes the agent produced. Audio is fetched with the token and played from a local blob (an <audio src> cannot send headers). */
export function Podcasts() {
  const [items, setItems] = useState<Episode[]>([]);
  const [playing, setPlaying] = useState<{ slug: string; url: string } | null>(null);
  const [err, setErr] = useState("");
  const url = useRef<string | null>(null);

  const load = () => api<Episode[]>("/api/podcasts").then(setItems).catch(() => {});
  useEffect(() => { load(); return () => { if (url.current) URL.revokeObjectURL(url.current); }; }, []);

  async function play(slug: string) {
    setErr("");
    try {
      const r = await fetch(`/api/podcasts/${slug}/audio`, { headers: { Authorization: `Bearer ${getToken()}` } });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      if (url.current) URL.revokeObjectURL(url.current);
      url.current = URL.createObjectURL(await r.blob());
      setPlaying({ slug, url: url.current });
    } catch (e) { setErr(`재생하지 못했어요: ${(e as Error).message}`); }
  }
  async function remove(slug: string) {
    if (!confirm("이 에피소드를 삭제할까요?")) return;
    await api(`/api/podcasts/${slug}`, { method: "DELETE" });
    if (playing?.slug === slug) setPlaying(null);
    load();
  }

  if (items.length === 0) return <p className="muted small" style={{ textAlign: "center", marginTop: 24 }}>아직 만든 팟캐스트가 없어요.</p>;
  return (
    <div className="card form">
      
      {items.map((e) => (
        <div key={e.slug} className="field">
          <span><strong>{e.title}</strong> {mmss(e.duration_secs)}</span>
          {e.description && <span className="small muted">{e.description}</span>}
          <span className="small muted">{e.speakers.join(", ")} · {new Date(e.created_at * 1000).toLocaleDateString()}</span>
          {playing?.slug === e.slug
            ? <audio controls autoPlay src={playing.url} style={{ width: "100%" }} />
            : <button type="button" onClick={() => play(e.slug)}>재생</button>}
          <button type="button" className="link danger" onClick={() => remove(e.slug)}>삭제</button>
        </div>
      ))}
      {err && <span className="error small">{err}</span>}
    </div>
  );
}

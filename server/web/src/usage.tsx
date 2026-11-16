import type { Usage, UsageWindow } from "./api";

const LABELS: [string, string][] = [
  ["Current session", "현재 세션"], ["Current week", "이번 주"], ["all models", "모든 모델"],
  ["Weekly Limit Remaining", "주간 한도"], ["Five Hour Limit Remaining", "5시간 한도"],
];
export const label = (s: string) => LABELS.reduce((acc, [en, ko]) => acc.replace(en, ko), s);
export const fmt = (n: number) => n.toLocaleString("ko-KR");

/** "약 5시간 후 · 11/6 06:00" in the viewer's own time zone. */
export function resetText(w: UsageWindow): string | null {
  if (!w.resets_at) return w.resets_text;
  const t = new Date(w.resets_at);
  if (Number.isNaN(t.getTime())) return w.resets_text;
  const mins = Math.round((t.getTime() - Date.now()) / 60000);
  const rel = mins <= 0 ? "곧" : mins < 60 ? `약 ${mins}분 후` : mins < 2880 ? `약 ${Math.round(mins / 60)}시간 후` : `약 ${Math.round(mins / 1440)}일 후`;
  return `${rel} · ${t.toLocaleString("ko-KR", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

export function UsageBar({ w, compact }: { w: UsageWindow; compact?: boolean }) {
  const reset = resetText(w);
  return (
    <div className="usage">
      <div className="between"><span>{label(w.label)}</span><b>{w.used_percent}% 사용</b></div>
      <div className="bar"><i className={w.used_percent >= 90 ? "hot" : ""} style={{ width: `${Math.min(100, w.used_percent)}%` }} /></div>
      <span className="small muted">{compact ? (reset ? `초기화 ${reset}` : "") : `남음 ${Math.max(0, 100 - w.used_percent)}%${reset ? ` · 초기화 ${reset}` : ""}`}</span>
    </div>
  );
}

/** "Gemini Models" -> "Gemini 모델", "Claude and GPT models" -> "Claude · GPT 모델". */
export const groupName = (g: string) => g.replace(/\s+models?$/i, " 모델").replace(/\s+and\s+/gi, " · ");

const STOP = new Set(["model", "models", "and", "the", "of"]);
/** The limit group that covers the model in use ("Gemini Models" for gemini-3.8-flash-high), or null when that cannot be told. */
export function activeGroup(groups: string[], model: string): string | null {
  const m = model.toLowerCase();
  const hits = groups.filter((g) => g.toLowerCase().split(/[^a-z0-9]+/).some((word) => word.length > 1 && !STOP.has(word) && m.includes(word)));
  return hits.length === 1 ? hits[0] : null;
}

export type UsageGroup = { group: string | null; name: string; active: boolean; windows: UsageWindow[] };

/** The limit windows split by the models they cover; the group of the model in use comes first. Empty when the provider reports none. */
export function usageGroups(u: Usage | null, model: string): UsageGroup[] {
  const sub = u?.subscription;
  if (!sub || sub.error) return [];
  const names = [...new Set(sub.windows.map((w) => w.group))];
  const active = activeGroup(names.filter((g): g is string => !!g), model);
  const groups = names.map((g) => ({ group: g, name: g ? groupName(g) : "", active: g !== null && g === active, windows: sub.windows.filter((w) => w.group === g) }));
  return [...groups.filter((g) => g.active), ...groups.filter((g) => !g.active)];
}

/**
 * The limits, one block per group of models with its name. Where the model in use is known, its group is first and marked, and (in the
 * short form) the other groups take a single line each with their percentages; otherwise every group is shown in full.
 */
export function UsageGroups({ groups, full }: { groups: UsageGroup[]; full?: boolean }) {
  const known = groups.some((g) => g.active);
  return (
    <>
      {groups.map((g) => (
        <div key={g.group ?? ""} className="usage-group">
          {g.group && <div className="usage-group-name"><b>{g.name}</b>{g.active && <span className="tag">지금 쓰는 모델</span>}</div>}
          {known && !full && !g.active
            ? <div className="small muted" style={{ paddingTop: 4 }}>{g.windows.map((w) => `${label(w.label)} ${w.used_percent}% 사용`).join(" · ")}</div>
            : g.windows.map((w) => <UsageBar key={w.label} w={w} compact={!full} />)}
        </div>
      ))}
    </>
  );
}

import type { ReactNode } from "react";

/** A deliberately small Markdown subset (bold, italic, `code`, links, lists, code blocks), built as React
 *  elements, never as HTML: model output can never inject markup. Headings are left as plain text. */

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\s][^*\n]*\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;

function inline(text: string): ReactNode[] {
  return text.split(INLINE).map((tok, i) => {
    if (tok.startsWith("`") && tok.endsWith("`") && tok.length > 2) return <code key={i}>{tok.slice(1, -1)}</code>;
    if (tok.startsWith("**") && tok.endsWith("**") && tok.length > 4) return <strong key={i}>{tok.slice(2, -2)}</strong>;
    if (tok.startsWith("*") && tok.endsWith("*") && tok.length > 2) return <em key={i}>{tok.slice(1, -1)}</em>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/.exec(tok);
    if (link) return <a key={i} href={link[2]} target="_blank" rel="noopener noreferrer">{link[1]}</a>;
    return tok;
  });
}

export function Md({ text, headings = false }: { text: string; headings?: boolean }) {
  const lines = text.split("\n");
  const out: ReactNode[] = [];
  let para: string[] = [];
  let i = 0;
  const flush = () => { if (para.length) { out.push(<p key={out.length}>{inline(para.join("\n"))}</p>); para = []; } };

  while (i < lines.length) {
    const line = lines[i];
    if (line.trim().startsWith("```")) {                       // fenced block; an unclosed one (still streaming) runs to the end
      flush();
      const body: string[] = [];
      for (i++; i < lines.length && !lines[i].trim().startsWith("```"); i++) body.push(lines[i]);
      i++;
      out.push(<pre key={out.length}><code>{body.join("\n")}</code></pre>);
      continue;
    }
    const head = headings ? /^(#{1,6})\s+(.*)$/.exec(line) : null;
    if (head) { flush(); out.push(<h4 key={out.length} className="doc-h">{inline(head[2])}</h4>); i++; continue; }
    const bullet = /^\s*[-*]\s+(.*)$/.exec(line), number = /^\s*\d+[.)]\s+(.*)$/.exec(line);
    if (bullet || number) {
      flush();
      const ordered = !!number, items: string[] = [];
      for (; i < lines.length; i++) {
        const m = (ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*]\s+(.*)$/).exec(lines[i]);
        if (!m) break;
        items.push(m[1]);
      }
      const li = items.map((t, k) => <li key={k}>{inline(t)}</li>);
      out.push(ordered ? <ol key={out.length}>{li}</ol> : <ul key={out.length}>{li}</ul>);
      continue;
    }
    if (line.trim() === "") flush(); else para.push(line);
    i++;
  }
  flush();
  return <>{out}</>;
}

const BREAK = "<message_break/>";

/** Split one reply into chat bubbles at <message_break/>; hides a half-typed tag while the reply streams. */
export function splitBubbles(text: string): string[] {
  let t = text;
  for (let n = Math.min(BREAK.length - 1, t.length); n >= 1; n--) {
    if (t.endsWith(BREAK.slice(0, n))) { t = t.slice(0, -n); break; }
  }
  return t.split(/<message_break\s*\/?>/).map((s) => s.trim()).filter(Boolean);
}

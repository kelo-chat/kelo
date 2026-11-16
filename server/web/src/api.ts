const TOKEN_KEY = "assistant.token";

export const getToken = () => localStorage.getItem(TOKEN_KEY) ?? "";
export const setToken = (t: string) => localStorage.setItem(TOKEN_KEY, t);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let onUnauthorized: () => void = () => {};
export const setUnauthorizedHandler = (fn: () => void) => { onUnauthorized = fn; };

async function failure(r: Response): Promise<ApiError> {
  let msg = `HTTP ${r.status}`;
  try {
    const j = await r.json();
    if (typeof j.detail === "string") msg = j.detail;
  } catch { /* keep default */ }
  if (r.status === 401) onUnauthorized();
  return new ApiError(r.status, msg);
}

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const { json, headers, ...rest } = init;
  const r = await fetch(path, {
    ...rest,
    headers: {
      Authorization: `Bearer ${getToken()}`,
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...headers,
    },
    body: json !== undefined ? JSON.stringify(json) : rest.body,
  });
  if (!r.ok) throw await failure(r);
  return r.json() as Promise<T>;
}

// ---- types
export type Profile = {
  user_name: string; agent_name: string; provider: string; model: string;
  fallback_provider: string; fallback_model: string; effort: string;
};
export type StoredMessage = { id: number; role: "user" | "assistant"; content: string; created_at: number };
export type Goal = { id: number; title: string; category: string; note: string; status: "tracking" | "done" };
export type Connector = { id: string; name: string; description: string; category: string;
  status: "connected" | "disabled" | "coming_soon" | "available" | "browser" | "autofill" | "unsupported";
  /** how it is connected here: a code typed on the service's page, its own sign-in page (MCP), or a mailbox app password */
  connect?: "device" | "mcp" | "mail" | "key" | "browser" | "autofill" | "google" | null; item?: string; via?: string; site?: string; steps?: string[];
  fallback?: { connect: "key"; via: string; item: string; blurb: string; connected: boolean } };
export type KeyForm = { id: string; name: string; item: string; help: string[]; checked: boolean; connected: boolean; label: string;
  fields: { id: string; label: string; secret: boolean; placeholder: string }[] };
export type MailProvider = { id: string; name: string; help: string[] };
export type MailAccount = { name: string; provider: string; label: string; address: string };
/** A service that is connected by typing a short code on its own page (OAuth device flow). The token goes to the vault; nothing here carries it. */
export type OAuthProvider = { id: string; item: string; name: string; has_client_id: boolean; connected: boolean; account: string; settings_url: string; web?: boolean;
  help: string[]; scopes: { id: string; label: string; detail: string }[] };
export type OAuthFlow = { id: string; provider: string; status: "pending" | "connected" | "denied" | "expired" | "error" | "cancelled"; message: string;
  account: string; user_code: string; verification_uri: string; expires_in: number };
export type ProviderInfo = { name: string; display_name: string; oauth: boolean; logged_in: boolean | null; configured: boolean | null;
  cli: { installed: boolean; installable: boolean; logged_in: boolean | null; hint: string;
          remote?: boolean; version?: string | null; update_hint?: string } | null;
  effort_levels?: string[]; effort_note?: string | null; takes_key: boolean; key_source: "app" | "env" | null; key_hint: string | null };
export type ModelList = { provider: string; models: string[]; labels?: Record<string, string>; error: string | null };
export type UsageWindow = { group: string | null; label: string; used_percent: number; resets_at: string | null; resets_text: string | null };
export type Usage = {
  today: { input_tokens: number; output_tokens: number }; total: { input_tokens: number; output_tokens: number };
  subscription: { provider: string; windows: UsageWindow[]; error: string | null } | null;
};

export type StreamEvent =
  | { type: "thinking" }
  | { type: "text"; text: string }
  | { type: "tool_started"; id: string; name: string; arguments: Record<string, unknown> }
  | { type: "tool_result"; id: string; name: string; output: string; is_error: boolean; approved: boolean | null }
  | { type: "approval_request"; approval_id: string; name: string; arguments: Record<string, unknown>; detail?: string }
  | { type: "step_limit"; steps: number }
  | { type: "fallback"; provider: string; model: string; reason: string }
  | { type: "retry"; reason: string }
  | { type: "reconnecting" }
  | { type: "nest_changed"; id: string }
  | { type: "done"; input_tokens: number; output_tokens: number; steps: number }
  | { type: "error"; message: string };

type TurnPoll = { events: StreamEvent[]; next: number; done: boolean };
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const GIVE_UP_AFTER = 30;   // failed requests in a row (about two minutes) before the connection counts as lost

/**
 * Start a reply, then follow it with short requests instead of one long stream. A stream can be cut on the way (a phone that
 * went to sleep, a router, a quick tunnel that does not support streaming); a request that fails is simply made again from the
 * same place, so nothing is lost and the reply keeps running on the server meanwhile. Resolves when the reply is over.
 */
export async function talk(text: string, onEvent: (e: StreamEvent) => void, signal: AbortSignal): Promise<void> {
  const { id } = await api<{ id: string }>("/api/turns", {
    method: "POST", signal, json: { text, timezone: Intl.DateTimeFormat().resolvedOptions().timeZone },
  });
  signal.addEventListener("abort", () => {   // the Stop button (or leaving): tell the server to stop working on it
    fetch(`/api/turns/${id}`, { method: "DELETE", headers: { Authorization: `Bearer ${getToken()}` } }).catch(() => {});
  }, { once: true });
  let after = 0;
  let failures = 0;
  for (;;) {
    let r: TurnPoll;
    try {
      r = await api<TurnPoll>(`/api/turns/${id}?after=${after}&wait=20`, { signal });
    } catch (e) {
      const transient = !(e instanceof ApiError) || e.status >= 500 || e.status === 408 || e.status === 429;   // no signal, a gateway hiccup
      if (signal.aborted || !transient || ++failures >= GIVE_UP_AFTER) throw e;
      if (failures === 2) onEvent({ type: "reconnecting" });
      await sleep(Math.min(500 * failures, 4000));
      continue;
    }
    failures = 0;
    for (const ev of r.events) onEvent(ev);
    after = r.next;
    if (r.done) return;
  }
}

/** Hand the agent a PDF / Word / Excel file. The file is the raw request body. */
export async function uploadDocument(file: File): Promise<string> {
  const r = await fetch(`/api/uploads/${encodeURIComponent(file.name)}`, {
    method: "PUT", headers: { Authorization: `Bearer ${getToken()}` }, body: file,
  });
  if (!r.ok) throw await failure(r);
  return (await r.json()).path as string;
}

/** A document the user uploaded. */
export type Upload = { path: string; bytes: number; modified: number };

/** Save one of the user's own uploaded documents to this device (fetched with the token, then handed to the browser as a download). */
export async function downloadUpload(name: string): Promise<void> {
  const r = await fetch(`/api/uploads/${encodeURIComponent(name)}/file`, { headers: { Authorization: `Bearer ${getToken()}` } });
  if (!r.ok) throw await failure(r);
  const url = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name;
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export const answerApproval = (id: string, approve: boolean) =>
  api<{ ok: boolean }>(`/api/approvals/${id}`, { method: "POST", json: { approve } });

// ---- devices: a phone joins with a one-time code (usually from a QR) and then holds a token of its own
export type Device = { id: number; name: string; created_at: number; last_seen: number | null };
export type PairStart = { code: string; expires_in: number; link: string; qr_svg: string };

/** A readable name for this device, e.g. "iPhone · Safari". */
export function deviceName(): string {
  const ua = navigator.userAgent;
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac"
    : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "기기";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Safari\//.test(ua) ? "Safari" : /Firefox\//.test(ua) ? "Firefox" : "";
  return browser ? `${os} · ${browser}` : os;
}

/** Exchange a one-time code for this device's own token. Resolves with the token. */
export async function pairWithCode(code: string): Promise<string> {
  const r = await fetch("/api/pair", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code, name: deviceName() }),
  });
  if (!r.ok) throw await failure(r);
  return (await r.json()).token as string;
}

// ---- the secure vault: website logins and service keys. Values go in through a form and never come back.
export type VaultItem = {
  name: string; label: string; kind: "api" | "login" | "email"; hosts: string[]; endpoint: string; has_secret: boolean; auto_allow: boolean;
  last_used: number | null; username_hint?: string; placement: { type?: string; header?: string; param?: string; via?: string };
  /** what the last real use showed: ok, failed (a sign-in the site turned down), rejected (a key the service refused), code_needed, blocked, unknown */
  last_result: string | null; last_result_at: number | null; last_result_detail: string;
};
/** `items`: what the vault really holds (a value is stored). `pending`: requests that have no value yet (the assistant asked for a login,
 *  or the user started adding one); they are not shown as part of the vault. */
export type VaultInfo = { locked: boolean; items: VaultItem[]; pending: VaultItem[]; entered_once: string[]; verifying: string[] };
export type VaultLogRow = { ts: number; item: string; action: string; detail: string; ok: boolean };

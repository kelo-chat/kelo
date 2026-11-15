import { useEffect, useState } from "react";
import { api, getToken, pairWithCode, setToken, setUnauthorizedHandler, type Profile } from "./api";
import { Avatar } from "./Avatar";
import { Chat } from "./Chat";
import { Connectors } from "./Connectors";
import { Goals } from "./Goals";
import { ChatIcon, GoalIcon, PlugIcon, UserIcon } from "./icons";
import { Login } from "./Login";
import { LanguagePick } from "./LanguagePick";
import { applyLanguage, savedLanguage } from "./i18n";
import { Onboarding } from "./Onboarding";
import { MyPage } from "./MyPage";

type Tab = "chat" | "goals" | "connectors" | "settings";
const TABS: [Tab, string, () => React.JSX.Element][] = [
  ["chat", "채팅", ChatIcon], ["goals", "목표", GoalIcon], ["connectors", "커넥터", PlugIcon], ["settings", "마이", UserIcon],
];

// A link like https://my.address/#pair=CODE signs this device in. Done once per page load, outside React, so a re-render cannot spend the code twice.
const PAIR_PREFIX = "#pair=";
const pairing: Promise<"ok" | string> | null = location.hash.startsWith(PAIR_PREFIX)
  ? (() => {
      const code = decodeURIComponent(location.hash.slice(PAIR_PREFIX.length));
      history.replaceState(null, "", location.pathname + location.search);   // the code does not stay in the address bar or history
      return pairWithCode(code).then((token) => { setToken(token); return "ok"; }, (e: Error) => e.message);
    })()
  : null;

/** True when the server now serves a newer build of this app than the page on screen (a long-open tab after an update). */
function useNewerBuild(authed: boolean): boolean {
  const [newer, setNewer] = useState(false);
  useEffect(() => {
    const mine = document.querySelector("script[type=module][src*='/assets/']")?.getAttribute("src");
    if (!authed || !mine) return;   // the dev server has no hashed file to compare
    let alive = true;
    const check = () => api<{ build?: string }>("/api/about")
      .then((a) => { if (alive && a.build && a.build !== mine) setNewer(true); }).catch(() => {});
    const onShow = () => { if (document.visibilityState === "visible") check(); };
    check();
    document.addEventListener("visibilitychange", onShow);
    return () => { alive = false; document.removeEventListener("visibilitychange", onShow); };
  }, [authed]);
  return newer;
}

const startLanguage = savedLanguage();
if (startLanguage) applyLanguage(startLanguage);

export default function App() {
  const [lang, setLang] = useState<string | null>(startLanguage);
  const [authed, setAuthed] = useState(!!getToken());
  const [pairState, setPairState] = useState<"idle" | "working" | string>(pairing ? "working" : "idle");
  const [profile, setProfile] = useState<Profile | null>(null);
  const [tab, setTab] = useState<Tab>("chat");
  const newer = useNewerBuild(authed);

  useEffect(() => { setUnauthorizedHandler(() => { setAuthed(false); setProfile(null); }); }, []);
  useEffect(() => {
    pairing?.then((r) => { if (r === "ok") setAuthed(true); setPairState(r === "ok" ? "idle" : r); });
  }, []);
  useEffect(() => {
    if (authed) api<Profile>("/api/profile").then(setProfile).catch(() => {});
  }, [authed]);

  if (!lang) return <LanguagePick onDone={setLang} />;   // the first screen on a new device (a pairing link is already being used meanwhile)
  if (pairState === "working") return <div className="center-card muted">이 기기를 연결하는 중…</div>;
  if (!authed) return <Login onDone={() => setAuthed(true)} notice={pairState !== "idle" ? pairState : undefined} />;
  if (!profile) return <div className="center-card muted">불러오는 중…</div>;
  if (!profile.agent_name) return <Onboarding initial={profile} onDone={setProfile} />;

  return (
    <div className="app">
      <header>
        <Avatar name={profile.agent_name} size={40} />
        <span className="agent-name">{profile.agent_name}</span>
      </header>
      {newer && <button className="update-bar" onClick={() => location.reload()}>새 버전이 있어요 · 눌러서 새로고침</button>}
      <main>
        {tab === "chat" && <Chat profile={profile} onProfile={setProfile} />}
        {tab === "goals" && <Goals />}
        {tab === "connectors" && <Connectors />}
        {tab === "settings" && <MyPage profile={profile} onProfile={setProfile} onLogout={() => { setAuthed(false); setProfile(null); }} />}
      </main>
      <nav>
        {TABS.map(([id, label, Icon]) => (
          <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)} aria-label={label}>
            <Icon /><span>{label}</span>
          </button>
        ))}
      </nav>
    </div>
  );
}

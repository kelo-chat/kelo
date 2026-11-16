import { useEffect, useState } from "react";
import { Devices } from "./Devices";
import { DocumentsPage } from "./DocumentsPage";
import { ModelPage } from "./ModelPage";
import { Podcasts } from "./Podcasts";
import { PrivacyPage } from "./PrivacyPage";
import { ProfilePage } from "./ProfilePage";
import { Resources } from "./Resources";
import { Group, Row, SectionLabel, SubPage } from "./Rows";
import { UsagePage } from "./UsagePage";
import { VaultPage } from "./Vault";
import { api, clearToken, type Profile, type Usage, type VaultInfo, type VaultItem } from "./api";
import { ChevronRight, ChipIcon, FileIcon, GlobeIcon, KeyIcon, MicIcon, PhoneIcon, PlusIcon, ShieldIcon, SparkIcon, TrashIcon, UserIcon } from "./icons";
import { languageName, saveLanguage, savedLanguage } from "./i18n";
import { LanguageList } from "./LanguagePick";
import { fmt, UsageGroups, usageGroups } from "./usage";

type View = "home" | "profile" | "model" | "usage" | "devices" | "vault" | "resources" | "documents" | "podcasts" | "privacy" | "language";

const SIZE_PROFILE: Record<string, string> = { recommended: "권장 설정", minimum: "최소 설정", unlimited: "서버 전체", custom: "직접 설정" };
const COLORS = ["#6366f1", "#0ea5e9", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6"];

type Overview = {
  usage: Usage | null; devices: number | null; creds: VaultItem[]; locked: boolean; documents: number | null; podcasts: number | null;
  sizes: string | null; version: string | null;
};

/**
 * The My page: how much has been used, who this is, what is connected, and the settings, one tap away each.
 * It shows only what belongs to the person (their account, their documents and podcasts, their devices): the skills,
 * reference documents and the assistant's own notes are the assistant's and are not listed here.
 */
export function MyPage({ profile, onProfile, onLogout }: { profile: Profile; onProfile: (p: Profile) => void; onLogout: () => void }) {
  const [view, setView] = useState<View>("home");
  const [o, setO] = useState<Overview>({ usage: null, devices: null, creds: [], locked: false, documents: null, podcasts: null, sizes: null, version: null });

  // the phone's back button / gesture steps out of a sub page, like a native settings screen
  useEffect(() => {
    if (history.state?.my) history.replaceState(null, "");
    const onPop = (e: PopStateEvent) => setView((e.state?.my as View) ?? "home");
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const go = (v: View) => { history.pushState({ my: v }, ""); setView(v); };
  const back = () => (history.state?.my ? history.back() : setView("home"));

  const refreshUsage = (refresh = false) => api<Usage>(`/api/usage${refresh ? "?refresh=true" : ""}`).then((usage) => setO((p) => ({ ...p, usage }))).catch(() => {});
  useEffect(() => {
    if (view !== "home") return;     // fresh numbers every time the My page is shown
    const put = (patch: Partial<Overview>) => setO((p) => ({ ...p, ...patch }));
    refreshUsage();
    api<{ devices: unknown[] }>("/api/devices").then((d) => put({ devices: d.devices.length })).catch(() => {});
    api<VaultInfo>("/api/vault").then((v) => put({ creds: v.items, locked: v.locked })).catch(() => {});
    api<unknown[]>("/api/uploads").then((d) => put({ documents: d.length })).catch(() => {});
    api<unknown[]>("/api/podcasts").then((d) => put({ podcasts: d.length })).catch(() => {});
    api<{ profile: string }>("/api/resources").then((r) => put({ sizes: SIZE_PROFILE[r.profile] ?? r.profile })).catch(() => {});
    api<{ version: string }>("/api/about").then((a) => put({ version: a.version })).catch(() => {});
  }, [view]); // eslint-disable-line react-hooks/exhaustive-deps

  async function clearChat() {
    if (confirm("대화 기록을 모두 삭제할까요? 되돌릴 수 없어요.")) { await api("/api/messages", { method: "DELETE" }); location.reload(); }
  }

  switch (view) {
    case "profile": return <ProfilePage profile={profile} onProfile={onProfile} onBack={back} />;
    case "model": return <ModelPage profile={profile} onProfile={onProfile} onBack={back} />;
    case "usage": return <UsagePage usage={o.usage} model={profile.model} onRefresh={() => refreshUsage(true)} onBack={back} />;
    case "devices": return <SubPage title="내 기기" onBack={back}><Devices onLogout={onLogout} /></SubPage>;
    case "vault": return <VaultPage onBack={back} onGoModel={() => go("model")} />;
    case "resources": return <SubPage title="Kelo 서버 자원" onBack={back}><Resources /></SubPage>;
    case "documents": return <DocumentsPage onBack={back} />;
    case "podcasts": return <SubPage title="팟캐스트" onBack={back}><Podcasts /></SubPage>;
    case "privacy": return <PrivacyPage onBack={back} />;
    case "language": return (
      <SubPage title="언어" onBack={back}>
        <LanguageList value={savedLanguage() ?? "ko"} onChange={(c) => { saveLanguage(c); location.reload(); }} />
      </SubPage>
    );
  }

  const agent = profile.agent_name;
  const connected = o.creds.filter((c) => c.has_secret && (c.kind === "api" || c.kind === "email"));
  const services = [...connected.map((c, i) => ({ key: c.name, text: (c.label || c.name).slice(0, 1).toUpperCase(), color: COLORS[i % COLORS.length] }))];
  const groups = usageGroups(o.usage, profile.model);
  return (
    <div className="page mypage">
      <h2>나의 {agent}</h2>

      <div className="card usage-card">
        <div className="between"><b>사용량 한도</b></div>
        {groups.length > 0 ? <UsageGroups groups={groups} /> : o.usage && !o.usage.subscription ? (
          <div className="usage-block">
            <span className="small muted">이 모델은 한도 정보를 주지 않아요 · 오늘 쓴 토큰</span>
            <b>{fmt(o.usage.today.input_tokens + o.usage.today.output_tokens)}</b>
          </div>
        ) : <span className="small muted">{o.usage?.subscription?.error ?? "불러오는 중…"}</span>}
        <button type="button" className="link-row" onClick={() => go("usage")}><span>사용량 자세히 보기</span><ChevronRight /></button>
      </div>

      <button type="button" className="info-card" onClick={() => go("privacy")}>
        <span className="menu-icon"><ShieldIcon /></span>
        <span className="grow"><div className="t">개인 정보를 안전하게 보호해요</div><div className="s">비밀번호와 키는 안전한 보관함에 암호화해 두고, 대화와 파일은 Kelo 서버에 있어요.</div></span>
        <span className="menu-chev"><ChevronRight /></span>
      </button>

      <button type="button" className="info-card" onClick={() => go("profile")}>
        <span className="menu-icon"><UserIcon /></span>
        <span className="grow"><div className="t">{profile.user_name || "이름을 정해 주세요"}</div><div className="s">내 계정</div></span>
        <span className="menu-chev"><ChevronRight /></span>
      </button>

      <button type="button" className="info-card column" onClick={() => go("vault")}>
        <div className="between" style={{ width: "100%" }}>
          <b>{agent}에 연결된 앱 및 서비스 <span className="muted">{services.length}개</span></b>
          <span className="menu-chev"><ChevronRight /></span>
        </div>
        <div className="app-icons">
          {services.slice(0, 5).map((s) => <span key={s.key} className="app-icon" style={{ background: s.color }}>{s.text}</span>)}
          <span className="app-icon plus"><PlusIcon /></span>
        </div>
      </button>

      <SectionLabel>{agent}</SectionLabel>
      <Group>
        <Row icon={<SparkIcon />} label="모델" value={profile.model || "서버 기본값"} onClick={() => go("model")} />
        <Row icon={<FileIcon />} label="내 문서" value={o.documents != null ? `${o.documents}개` : ""} onClick={() => go("documents")} />
        {(o.podcasts ?? 0) > 0 && <Row icon={<MicIcon />} label="팟캐스트" value={`${o.podcasts}개`} onClick={() => go("podcasts")} />}
      </Group>

      <SectionLabel>연결과 보안</SectionLabel>
      <Group>
        <Row icon={<KeyIcon />} label="안전한 보관함"
             value={o.creds.some((c) => c.last_result === "failed" || c.last_result === "rejected") ? <span className="error">확인 필요</span> : o.locked ? "잠김" : o.creds.length ? `${o.creds.length}개` : ""}
             onClick={() => go("vault")} />
        <Row icon={<PhoneIcon />} label="내 기기" value={o.devices != null ? `${o.devices}대` : ""} onClick={() => go("devices")} />
        <Row icon={<ChipIcon />} label="Kelo 서버 자원" value={o.sizes ?? ""} onClick={() => go("resources")} />
      </Group>

      <SectionLabel>앱</SectionLabel>
      <Group>
        <Row icon={<GlobeIcon />} label="언어" value={languageName(savedLanguage())} onClick={() => go("language")} />
      </Group>

      <SectionLabel>데이터 관리</SectionLabel>
      <Group>
        <Row icon={<TrashIcon />} label="대화 기록 삭제" onClick={clearChat} danger />
      </Group>

      {o.version && <p className="version">앱 버전: {o.version}</p>}
      <button type="button" className="logout" onClick={() => { clearToken(); onLogout(); }}>로그아웃</button>
    </div>
  );
}

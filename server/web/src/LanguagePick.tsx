import { useState } from "react";
import { GlobeIcon } from "./icons";
import { LANGUAGES, saveLanguage, suggestedLanguage } from "./i18n";

/** The list itself: every language under its own name, the device's language picked to start with. */
export function LanguageList({ value, onChange }: { value: string; onChange: (code: string) => void }) {
  return (
    <div className="menu-card lang-list" role="radiogroup" aria-label="Language">
      {LANGUAGES.map((l) => (
        <button key={l.code} type="button" role="radio" aria-checked={value === l.code} lang={l.code}
                className={`menu-row${value === l.code ? " on" : ""}`} onClick={() => onChange(l.code)}>
          <span className="menu-label">{l.name}</span>
          <span className="lang-check" aria-hidden>{value === l.code ? "✓" : ""}</span>
        </button>
      ))}
    </div>
  );
}

/** The very first screen: choose the app's language. Shown once per device, before anything else. */
export function LanguagePick({ onDone }: { onDone: (code: string) => void }) {
  const [code, setCode] = useState(suggestedLanguage());
  return (
    <div className="center-card">
      <span className="lang-icon"><GlobeIcon /></span>
      <h1>Language</h1>
      <LanguageList value={code} onChange={setCode} />
      <button type="button" className="primary" onClick={() => { saveLanguage(code); onDone(code); }}>→</button>
    </div>
  );
}

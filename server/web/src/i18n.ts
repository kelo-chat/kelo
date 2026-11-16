/** The languages the app can be shown in. Adding one: an entry here (and, later, its strings). The choice is kept on this device. */
export type Language = { code: string; name: string };   // name in the language itself, so anyone can find their own

export const LANGUAGES: Language[] = [
  { code: "ko", name: "한국어" },
];

const KEY = "kelo.lang";

export function savedLanguage(): string | null {
  try {
    const v = localStorage.getItem(KEY);
    return v && LANGUAGES.some((l) => l.code === v) ? v : null;
  } catch {
    return null;
  }
}

/** The language this device's settings ask for, if the app has it; otherwise the first one. */
export function suggestedLanguage(): string {
  const wanted = (navigator.languages ?? [navigator.language]).map((x) => x.toLowerCase().split("-")[0]);
  return wanted.find((w) => LANGUAGES.some((l) => l.code === w)) ?? LANGUAGES[0].code;
}

export function applyLanguage(code: string): void {
  document.documentElement.lang = code;   // text wrapping and fonts follow it (see :lang rules in styles.css)
}

export function saveLanguage(code: string): void {
  try { localStorage.setItem(KEY, code); } catch { /* still applied for this visit */ }
  applyLanguage(code);
}

export const languageName = (code: string | null) => LANGUAGES.find((l) => l.code === code)?.name ?? "";

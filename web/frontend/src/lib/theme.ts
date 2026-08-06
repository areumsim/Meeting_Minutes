/**
 * 라이트/다크 테마 — 단일 소스.
 *
 * 규칙:
 *  - 기본값은 **OS 설정 따름**("system"). 사용자가 [설정] 또는 모바일 [더보기]에서
 *    라이트/다크로 고정할 수 있고, 그 선택만 localStorage 에 남는다.
 *  - 신호는 `<html data-theme="light|dark">` 하나다. CSS 는 `prefers-color-scheme` 을
 *    보지 않는다 — 미디어쿼리를 신호로 쓰면 "OS 는 다크인데 앱은 라이트로 고정"이 불가능하다.
 *  - iOS(Capacitor)는 웹뷰 밖의 상태바·배경도 함께 바꿔야 한다. 안 바꾸면 다크 화면 위에
 *    흰 상태바가 남고 스크롤 바운스에서 흰 띠가 보인다(PRD §4.3).
 *
 * 색 값은 여기 두 곳(웹뷰 밖이라 CSS 토큰을 읽을 수 없다)과 index.css 에 있다 —
 * 바꿀 땐 두 곳을 같이 고친다. 그래서 상수 이름에 토큰 이름을 그대로 적어 둔다.
 */

export type ThemeChoice = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "MM_THEME";

/** index.css 의 --color-bg 와 같은 값이어야 한다(네이티브 상태바·배경용). */
const NATIVE_BG: Record<ResolvedTheme, string> = {
  light: "#f7f6f3",
  dark: "#0f1011",
};

const prefersDark = (): boolean => {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false;
  } catch {
    return false;
  }
};

export function getThemeChoice(): ThemeChoice {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch { /* 프라이빗 모드 등 — 기본값으로 */ }
  return "system";
}

export function resolveTheme(choice: ThemeChoice = getThemeChoice()): ResolvedTheme {
  if (choice === "system") return prefersDark() ? "dark" : "light";
  return choice;
}

/**
 * 네이티브 셸(iOS) 동기화. Capacitor 가 없는 환경(PC 브라우저)에서는 조용히 넘어간다 —
 * 동적 import 라 웹 번들에 플러그인 코드가 실행되지도 않는다.
 */
async function syncNative(theme: ResolvedTheme): Promise<void> {
  try {
    const { Capacitor } = await import("@capacitor/core");
    if (!Capacitor.isNativePlatform()) return;
    const { StatusBar, Style } = await import("@capacitor/status-bar");
    // 라이트 배경 위에는 **어두운 글자**의 상태바가 온다(Style.Dark = dark content).
    await StatusBar.setStyle({ style: theme === "dark" ? Style.Light : Style.Dark });
    await StatusBar.setBackgroundColor({ color: NATIVE_BG[theme] }).catch(() => {});
  } catch { /* 플러그인 미설치·웹 — 무시 */ }
}

/** `<html data-theme>` 를 갱신하고 네이티브 셸을 맞춘다. */
export function applyTheme(choice: ThemeChoice = getThemeChoice()): ResolvedTheme {
  const theme = resolveTheme(choice);
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  // 폼 컨트롤·스크롤바 등 브라우저가 그리는 UI 도 함께 맞춘다(체크박스·select 화살표).
  root.style.colorScheme = theme;
  void syncNative(theme);
  return theme;
}

/** 사용자의 선택을 저장하고 즉시 반영한다. */
export function setThemeChoice(choice: ThemeChoice): ResolvedTheme {
  try { localStorage.setItem(STORAGE_KEY, choice); } catch { /* ignore */ }
  const theme = applyTheme(choice);
  // 같은 탭의 다른 컴포넌트(설정 화면과 더보기 시트)가 서로의 변경을 알아야 한다 —
  // storage 이벤트는 **다른 탭에만** 오므로 자체 이벤트를 쓴다.
  window.dispatchEvent(new CustomEvent("mm:theme", { detail: { choice, theme } }));
  return theme;
}

/**
 * 앱 시작 시 1회. `main.tsx` 에서 렌더 전에 부른다(첫 페인트 전에 적용해야 흰 화면이
 * 번쩍이지 않는다). OS 설정이 도중에 바뀌면 "system" 일 때만 따라간다.
 */
export function initTheme(): () => void {
  applyTheme();
  let mq: MediaQueryList | null = null;
  const onChange = () => { if (getThemeChoice() === "system") applyTheme("system"); };
  try {
    mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", onChange);
  } catch { /* 구형 브라우저 — 초기 적용만으로 충분 */ }
  return () => { try { mq?.removeEventListener("change", onChange); } catch { /* ignore */ } };
}

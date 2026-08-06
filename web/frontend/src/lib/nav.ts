/**
 * 정보구조(IA) 단일 소스 — PRD §4.1.
 *
 * leaf 5 + 설정 + 도움말. **회의 상세는 내비 항목이 아니다**(리뷰 P1-3) — 라이브러리 행·
 * 지식 그래프·위키링크에서만 들어가는 레코드 문맥 뷰라서 `NAV_OF` 로 라이브러리에 매핑한다.
 *
 * 화면과 세그먼트를 한 타입으로 묶는 이유: 도움말의 "바로 열기" 같은 딥링크가 `{view, tab}`
 * 한 값으로 이동해야 하기 때문이다. 종전 도움말은 옛 화면 이름(upload/recorder/wiki…)을
 * 문자열로 넘겨서, IA 가 바뀌면 그 링크들이 조용히 아무 데도 가지 않게 된다.
 */

export type View =
  | "library" | "create" | "detail" | "knowledge" | "prepare" | "settings" | "help";

export type CreateTab = "record" | "upload" | "text";
export type KnowledgeTab = "ask" | "graph";
export type PrepareTab = "prep" | "assistant";

/** 딥링크 한 개 — 화면 + (있으면) 그 화면의 세그먼트. */
export interface Destination {
  view: View;
  tab?: CreateTab | KnowledgeTab | PrepareTab;
  /** 지식 그래프로 갈 때 자동 검색할 대상(위키링크 클릭). */
  query?: string;
}

/** 내비에서 활성으로 보일 항목 — 상세는 라이브러리, 그 외는 자기 자신. */
export const navKeyOf = (view: View): View => (view === "detail" ? "library" : view);

/** 화면 제목·부제(topbar). 부제는 화면이 계산해 덮어쓸 수 있다. */
export const VIEW_TITLE: Record<View, { title: string; subtitle?: string }> = {
  library: { title: "라이브러리", subtitle: "모든 회의록" },
  create: { title: "새로 만들기", subtitle: "녹음 · 업로드 · 텍스트" },
  detail: { title: "회의 상세" },
  knowledge: { title: "지식", subtitle: "질문 · 그래프" },
  prepare: { title: "준비 · 비서", subtitle: "브리핑 · 자동화" },
  settings: { title: "설정" },
  help: { title: "도움말" },
};

export const CREATE_TABS: { key: CreateTab; label: string }[] = [
  { key: "record", label: "실시간 녹음" },
  { key: "upload", label: "파일 업로드" },
  { key: "text", label: "텍스트" },
];

export const KNOWLEDGE_TABS: { key: KnowledgeTab; label: string }[] = [
  { key: "ask", label: "질문" },
  { key: "graph", label: "그래프" },
];

export const PREPARE_TABS: { key: PrepareTab; label: string }[] = [
  { key: "prep", label: "회의 준비" },
  { key: "assistant", label: "비서" },
];

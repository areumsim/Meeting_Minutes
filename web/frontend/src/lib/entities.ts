/**
 * 엔티티 표시 규약 — 지식그래프 노드와 근거 출처의 **라벨·색·아이콘 단일 소스**.
 *
 * 왜 한 곳인가 — 같은 개념의 표가 세 벌로 갈라져 있었다:
 *   · `MiniGraph.TYPE_COLOR`      8종, 원시 hex
 *   · `GraphExplorer.TYPE_TONE`   6종, tailwind 클래스 — **타입 집합조차 달랐다**
 *                                  (`document` 가 있고 decision/action/note 가 없었다)
 *   · `SessionDetail.GRAPH_TYPE_LABELS` 7종, 한국어 라벨만
 * 그래서 같은 '노트' 노드가 상세 화면과 지식 화면에서 다른 색으로 보였다. PRD 리뷰 P2-3 이
 * 지적한 "두 그래프 렌더러가 팔레트를 공유할 것"이 이 파일이다.
 *
 * 색은 CSS 토큰(`--color-ent-*`)만 참조한다 — 다크 모드에서 명도가 바뀌어야 하는데
 * hex 를 박으면 라이트 값이 그대로 남는다. SVG 는 `var(...)` 문자열을, DOM 은 style 속성을
 * 쓰므로 두 형태를 모두 노출한다.
 */

import {
  Building2, User, Tag as TagIcon, CalendarDays, FileText, FolderKanban,
  GraduationCap, Globe, Archive, type LucideIcon,
} from "lucide-react";

/** 그래프 노드 타입 1종의 표시 규약. */
export interface EntityStyle {
  /** 한국어 라벨 — 섹션 제목·`aria-label`·칩에 쓴다. */
  label: string;
  /** `var(--color-ent-*)` — SVG fill/stroke, CSS 변수 자리 어디에나 넣을 수 있다. */
  color: string;
  icon: LucideIcon;
}

/**
 * decision·action 은 **자기 hue 를 갖지 않는다.** 색맹 안전 팔레트에서 status
 * (red/violet/green/gray)와 accent(teal)를 피하고 남는 hue 가 6개뿐이라, 두 타입에
 * 새 색을 주면 반드시 상태색과 부딪힌다(PRD §5.1 "status hue 재사용 금지").
 * 대신 소속을 물려받는다 — 결정은 회의에, 액션은 사람에 붙는 것이라 의미도 맞는다.
 */
const ENTITY: Record<string, EntityStyle> = {
  organization: { label: "조직", color: "var(--color-ent-org)", icon: Building2 },
  person: { label: "인물", color: "var(--color-ent-person)", icon: User },
  topic: { label: "주제", color: "var(--color-ent-topic)", icon: TagIcon },
  meeting: { label: "회의", color: "var(--color-ent-meeting)", icon: CalendarDays },
  note: { label: "노트", color: "var(--color-ent-note)", icon: FileText },
  document: { label: "문서", color: "var(--color-ent-note)", icon: FileText },
  project: { label: "프로젝트", color: "var(--color-ent-project)", icon: FolderKanban },
  decision: { label: "결정", color: "var(--color-ent-meeting)", icon: CalendarDays },
  action: { label: "액션", color: "var(--color-ent-person)", icon: User },
};

/** 서버가 새 타입을 추가해도 화면이 비지 않게 — 모르는 타입은 중립으로 그리고 원문을 보여준다. */
const UNKNOWN_ENTITY: EntityStyle = {
  label: "", color: "var(--color-ink-3)", icon: FileText,
};

export function entityStyle(type: string): EntityStyle {
  return ENTITY[type] || { ...UNKNOWN_ENTITY, label: type || "기타" };
}

export const entityLabel = (type: string): string => entityStyle(type).label;
export const entityColor = (type: string): string => entityStyle(type).color;

/** 그래프 범례용 — 실제로 쓰이는 6색만(파생 2종은 소속 색과 같아 범례에 중복 표기하지 않는다). */
export const ENTITY_LEGEND: { type: string; label: string; color: string }[] =
  ["organization", "person", "topic", "meeting", "note", "project"]
    .map((t) => ({ type: t, label: ENTITY[t].label, color: ENTITY[t].color }));

// ── 근거 출처 ──────────────────────────────────────────────────────────
/**
 * 관련 노트·페르소나 근거의 출처 표시.
 *
 * 정본은 파이썬 쪽 `wiki_core/realtime_search.py: SOURCE_ICON` + `personas.EV_REGISTRY`
 * 의 **값**(note/paper/web/registry)이다 — 값을 바꿀 땐 그쪽을 먼저 고친다.
 *
 * 이모지(📄🎓🌐🗂)를 쓰지 않는다: Windows 버전마다 렌더가 달라지고 스크린리더가
 * "서류철"처럼 읽는다(PRD §5.3). 라인 아이콘 + 텍스트 라벨을 병기한다.
 */
export interface SourceStyle {
  label: string;
  icon: LucideIcon;
}

const SOURCE: Record<string, SourceStyle> = {
  note: { label: "노트", icon: FileText },
  paper: { label: "논문", icon: GraduationCap },
  web: { label: "웹", icon: Globe },
  registry: { label: "지난 회의", icon: Archive },
};

export function sourceStyle(source?: string): SourceStyle {
  return SOURCE[source || "note"] || SOURCE.note;
}

export const sourceLabel = (source?: string): string => sourceStyle(source).label;

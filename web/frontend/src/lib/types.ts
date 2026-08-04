export interface Session {
  id: string;
  title: string;
  topic: string;
  date: string;
  type: string;
  status: string;
  language: string;
  translate: number;
  model: string;
  speakers: string;
  source: string;
  mode: string;
  cost_estimate: number;
  duration_sec: number;
  error_detail?: string | null;
  /** 실제로 전사를 만든 STT 제공자(폴백이 일어나면 여러 개). 구버전 DB 에는 없다. */
  stt_provider?: string | null;
  /** 다른 벤더로 폴백해 처리됐는지. 회의 음성이 어디로 갔는지 사용자가 알아야 한다. */
  stt_fallback_used?: number | null;
  created_at: string;
}

export interface Segment {
  id: string;
  session_id: string;
  speaker: string;
  text: string;
  translated_text: string;
  start_time: number;
  end_time: number;
}

export interface Document {
  id: string;
  session_id: string;
  type: string;
  content: string;
  format: string;
}

export interface Profile {
  name: string;
  description: string;
  source: string;
  type: string;
  language: string;
  translate: boolean;
  model: string;
  llm: string;
}

export interface GraphNode {
  id: string;
  type: string;
  label: string;
  attributes: Record<string, any>;
}

export interface GraphEdge {
  id: string;
  from_node_id: string;
  to_node_id: string;
  relation_type: string;
}

export interface SessionGraph {
  nodes: Record<string, GraphNode[]>;
  edges: GraphEdge[];
  node_count: number;
  edge_count: number;
}

export interface GraphNeighbors {
  node: GraphNode | null;
  edges: GraphEdge[];
  neighbors: GraphNode[];
}

export interface RealtimeSegment {
  // 렌더링 key용 안정 식별자 — 텍스트가 스트리밍으로 변해도 행이 리마운트되지 않게 한다
  id?: string;
  text: string;
  translatedText?: string;
  speaker: string;
  start: number;
  end: number;
  // 2-pass 보정: true면 빠른 패스 임시 조각(흐리게 표시) — revise 이벤트가 문장으로 교체
  provisional?: boolean;
}

export const MODE_PRESETS: Record<number, { label: string; language: string; translate: boolean; type: string }> = {
  1: { label: "한국어 회의", language: "ko", translate: false, type: "meeting" },
  2: { label: "영어 회의 (→한국어 번역)", language: "en", translate: true, type: "meeting" },
  3: { label: "영어 회의 (번역 없음)", language: "en", translate: false, type: "meeting" },
  4: { label: "세미나 (영어→한국어)", language: "en", translate: true, type: "seminar" },
  5: { label: "강의 (영어→한국어)", language: "en", translate: true, type: "lecture" },
  6: { label: "한국어 세미나", language: "ko", translate: false, type: "seminar" },
  7: { label: "한국어 강의", language: "ko", translate: false, type: "lecture" },
};

/** 회의 진행 페르소나 키 — 서버 `wiki_core/personas.py` 의 레지스트리 키와 1:1. */
export type PersonaKey =
  | "facilitator" | "scribe" | "domain_expert" | "fact_checker"
  | "devils_advocate" | "junior" | "senior" | "critic";

/**
 * 페르소나 개입 1건 (WS `facilitation`, PRD §8).
 *
 * 항상 `draft: true` — 보조 제안이라는 라벨을 코드로 고정한다. 화자 이름은 절대
 * 실려 오지 않는다(서버 COMMON_RULES 가 프롬프트에서 금지 + 대조 표현만 허용).
 */
export interface Facilitation {
  type?: "facilitation";
  id: string;
  persona: PersonaKey | string;
  personaLabel: string;
  /** 이 개입을 낸 참견도(3=표준 자동 카드, 2=소극 — [지금 점검]으로 모아 표시) */
  level: number;
  kind: "flow" | "missing" | "question" | "counterpoint" | "contrast" | string;
  risk?: "low" | "medium" | "high" | string;
  text: string;
  evidence?: { source: "note" | "web" | string; title: string; url?: string; score?: number; snippet?: string }[];
  /** 근거가 된 발화 구간(초) — 전사 패널 점프용 */
  span?: { t0: number; t1: number };
  /** 트리아지가 근거로 든 발화 인용(짧게) */
  quote?: string;
  confidence?: number;
  /**
   * 이 개입 1건의 금액(USD, 서버가 실효 모델 단가로 계산해 실어 보낸다).
   * 개입은 시간에 비례하지 않아 분당 요율로 표현할 수 없다 — 러닝 미터는 이 값을
   * 받은 만큼만 합산한다(추정이 아니라 실제 발생분).
   */
  costUsd?: number;
  /** 팩트체커·도메인만 의미 있음: 라이브 웹검색 성공 여부(M2). M1 은 항상 false */
  searched?: boolean;
  draft: true;
}

/**
 * 페르소나 채널 상태 (WS `facilitation_status`).
 * 한도·예산으로 개입이 멈춘 사실을 화면에 남기기 위한 이벤트 — 조용히 꺼지면
 * 사용자는 "기능이 없다"고 판단한다(이 리포 반복 규칙).
 */
export interface FacilitationStatus {
  type?: "facilitation_status";
  /** jump 는 프런트 자체 안내(밀려난 발화로 점프 불가) — 서버가 보내지 않는다. */
  kind: "blocked" | "capped" | "budget" | "pending" | "empty" | "jump" | string;
  message: string;
  pending?: number;
}

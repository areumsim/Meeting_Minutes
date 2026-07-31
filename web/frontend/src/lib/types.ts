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

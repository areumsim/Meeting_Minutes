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
  text: string;
  translatedText?: string;
  speaker: string;
  start: number;
  end: number;
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

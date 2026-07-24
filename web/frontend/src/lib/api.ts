import { sessionsStore, segmentsStore, documentsStore } from './db';
import type { Session, Segment, Document as Doc, SessionGraph, GraphNeighbors, GraphNode } from './types';

// API 호출 base 해석기 — 상대경로("/api/...")를 백엔드로 보낸다.
//  • exe가 프런트를 직접 서빙(PC 브라우저): getBackendUrl()="" → 상대경로 그대로(동일 오리진).
//  • 모바일 앱 '단독 모드': "" → 앱 자신(대개 미사용 경로는 catch로 흡수).
//  • 모바일 앱 'PC 연결 모드': getBackendUrl()="http://192.168.x.x:8501" → 그 PC로 전송.
// 절대 URL(https://api.openai.com, ws://...)은 그대로 통과시킨다.
function apiFetch(input: string, init?: RequestInit): Promise<Response> {
  const url = input.startsWith("/") ? `${getBackendUrl()}${input}` : input;
  return fetch(url, init);
}

// Authentication & Config Storage (localStorage for simple key/values)
export const getApiKey = () => localStorage.getItem("OPENAI_API_KEY") || "";
export const setApiKey = (key: string) => localStorage.setItem("OPENAI_API_KEY", key);

export const getAnthropicKey = () => localStorage.getItem("ANTHROPIC_API_KEY") || "";
export const setAnthropicKey = (key: string) => localStorage.setItem("ANTHROPIC_API_KEY", key);

export const getTargetEmail = () => localStorage.getItem("TARGET_EMAIL") || "";
export const setTargetEmail = (email: string) => localStorage.setItem("TARGET_EMAIL", email);

const DEFAULT_CONFIG = {
  models: {
    stt: "whisper-1",
    gpt_model: "gpt-4o-mini",
    claude_model: "claude-3-5-sonnet-20241022",
    translate_model: "gpt-4o-mini",
  },
  realtime: {
    ws_vad_type: "server_vad",
    ws_noise_reduction: "near_field"
  }
};

// 패키지(exe) 모드 감지 — FastAPI 백엔드가 같은 오리진에 함께 떠 있거나,
// 모바일 앱에서 PC(exe) 주소를 지정해 연결돼 있으면 true.
// 1회 확인 후 캐시한다. 모바일에서 서버 주소를 바꾸면 resetPackagedMode()로 무효화.
let _packagedMode: boolean | null = null;
export async function isPackagedMode(): Promise<boolean> {
  if (_packagedMode !== null) return _packagedMode;
  _packagedMode = await backendAvailable();
  return _packagedMode;
}

// 서버 연결 상태 캐시 무효화 — 모바일 앱에서 PC 주소를 바꾸거나 해제할 때 호출.
export function resetPackagedMode(): void {
  _packagedMode = null;
}

// 임의 URL의 백엔드 헬스체크 — 모바일 '연결 테스트'용(현재 설정과 무관하게 확인).
export async function testBackendUrl(url: string): Promise<{ ok: boolean; message: string }> {
  const base = (url || "").trim().replace(/\/+$/, "");
  if (!base) return { ok: false, message: "주소를 입력하세요." };
  if (!/^https?:\/\//i.test(base)) return { ok: false, message: "http:// 또는 https:// 로 시작해야 합니다." };
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 4000);
    const res = await fetch(`${base}/api/health`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) return { ok: false, message: `서버 응답 오류 (${res.status})` };
    const d = await res.json().catch(() => ({}));
    return { ok: true, message: `연결됨${d?.ffmpeg_available === false ? " (ffmpeg 없음 경고)" : ""}` };
  } catch (e: any) {
    return { ok: false, message: `연결 실패: PC가 켜져 있고 같은 WiFi인지, 주소·포트가 맞는지 확인하세요.` };
  }
}

export const getConfig = async () => {
  // 패키지 모드: config.json 이 단일 진실. GET /api/config (키는 마스킹되어 옴).
  if (await isPackagedMode()) {
    try {
      const res = await apiFetch("/api/config");
      if (res.ok) {
        const cfg = await res.json();
        if (cfg && !cfg.error) return cfg;
      }
    } catch { /* 폴백 */ }
  }
  const local = localStorage.getItem("APP_CONFIG");
  if (local) return JSON.parse(local);
  return DEFAULT_CONFIG;
};

export const updateConfig = async (data: any) => {
  // 패키지 모드: PUT /api/config 로 config.json 에 저장. 마스킹된(***) 값은 서버가 스킵.
  if (await isPackagedMode()) {
    const res = await apiFetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new Error(d?.detail || `설정 저장 실패 (${res.status})`);
    }
    return res.json();
  }
  localStorage.setItem("APP_CONFIG", JSON.stringify(data));
  return { success: true };
};

// 민감 값(키·비번)의 실제 평문을 서버에서 가져온다 — '보이기'용.
// 서버는 이 PC(localhost)에서 온 요청에만 실제 값을 준다. 모바일 PC연결 등 LAN
// 클라이언트는 403 → null 반환(계속 마스킹 표시). 실패/미지원도 null.
export async function revealSecret(path: string): Promise<string | null> {
  try {
    const res = await apiFetch(`/api/config/reveal?path=${encodeURIComponent(path)}`);
    if (!res.ok) return null;
    const d = await res.json();
    return typeof d?.value === "string" ? d.value : null;
  } catch {
    return null;
  }
}

// config 스키마(웹 Settings 자동 렌더링용) — 백엔드가 있을 때만 제공.
// 백엔드는 { version, groups } 형태로 주므로 groups 배열만 반환한다.
export const getConfigSchema = async (): Promise<any[] | null> => {
  if (!(await isPackagedMode())) return null;
  try {
    const res = await apiFetch("/api/config/schema");
    if (res.ok) {
      const data = await res.json();
      return Array.isArray(data) ? data : (data?.groups ?? null);
    }
  } catch { /* ignore */ }
  return null;
};

// 연결 테스트 — 백엔드 엔드포인트 호출. { ok, message } (한국어) 반환.
export const testOpenAIKey = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/openai", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `연결 테스트 실패: ${e?.message || e}` };
  }
};

export const testAnthropicKey = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/anthropic", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `연결 테스트 실패: ${e?.message || e}` };
  }
};

export const testObsidianPath = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/obsidian", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `연결 테스트 실패: ${e?.message || e}` };
  }
};

// 메일 연결 테스트 — SMTP 로그인 후 테스트 메일 1통 발송.
export const testEmail = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/email", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `메일 테스트 실패: ${e?.message || e}` };
  }
};

// Slack/Teams Webhook 연결 테스트 — 테스트 메시지 발송.
export const testSlack = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/slack", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `Slack 테스트 실패: ${e?.message || e}` };
  }
};
export const testTeams = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/config/test/teams", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `Teams 테스트 실패: ${e?.message || e}` };
  }
};

// 네이티브 폴더 선택 다이얼로그 — 서버(이 PC)에서 폴더 브라우저를 띄우고 선택 경로를 받는다.
// 패키지(로컬 백엔드) 모드에서만 동작. 취소/실패 시 { ok:false }.
export const pickFolder = async (initial?: string): Promise<{ ok: boolean; path?: string; message?: string; cancelled?: boolean }> => {
  try {
    const res = await apiFetch("/api/system/pick-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ initial: initial || "" }),
    });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `폴더 선택 실패: ${e?.message || e}` };
  }
};

// 업로드/배치 처리 진행 상태 — 처리 중 폴링용. (백엔드 배치 모드 전용)
export const getUploadProgress = async (
  sessionId: string
): Promise<{ found: boolean; percent?: number; stage?: string; elapsed?: number }> => {
  try {
    const res = await apiFetch(`/api/upload/progress/${sessionId}`);
    if (res.ok) return await res.json();
  } catch { /* 백엔드 없음/무시 */ }
  return { found: false };
};

// 진행 중인 업로드 처리 취소 요청 — 파이프라인이 다음 단계 경계에서 협조적으로 중단.
export const cancelUpload = async (
  sessionId: string
): Promise<{ ok: boolean; message?: string }> => {
  try {
    const res = await apiFetch(`/api/upload/cancel/${sessionId}`, { method: "POST" });
    if (res.ok) return await res.json();
  } catch { /* 백엔드 없음 */ }
  return { ok: false, message: "취소 요청 실패" };
};

// 비용 추정 — 세션 최종 비용(USD) / 실시간 요율.
export interface SessionCost { ok: boolean; duration_sec?: number; stt?: number; translate?: number; minutes?: number; total?: number; stt_model?: string; }
export const getSessionCost = async (sessionId: string): Promise<SessionCost | null> => {
  try {
    const res = await apiFetch(`/api/sessions/${sessionId}/cost`);
    if (res.ok) return await res.json();
  } catch { /* 백엔드 없음 */ }
  return null;
};
export interface CostRates { stt_model: string; stt_per_min: number; translate_per_min: number; minutes_flat: number; }
export const getCostRates = async (): Promise<CostRates | null> => {
  try {
    const res = await apiFetch(`/api/cost/rates`);
    if (res.ok) return await res.json();
  } catch { /* 백엔드 없음 */ }
  return null;
};

// 앱(서버) 종료 — 콘솔 창 없는 배포에서 웹으로 깔끔히 끄기.
export const shutdownApp = async (): Promise<boolean> => {
  try {
    const res = await apiFetch("/api/shutdown", { method: "POST" });
    return res.ok;
  } catch {
    return true; // 서버가 즉시 죽어 응답이 끊길 수 있음 → 성공으로 간주
  }
};

// 볼트 인덱스 재빌드 — folder-only 위키 검색을 위해 .md 폴더를 다시 색인.
export const reindexVault = async (): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/reindex", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `재빌드 실패: ${e?.message || e}` };
  }
};

// 폴더 자동 감시(vault_watcher) — 서버 내장 감시 제어/상태 (패키지 모드 전용).
export interface WatcherStatus {
  running: boolean;
  config_enabled: boolean;
  folders: string[];
  counts: { done: number; failed: number; processing: number; skipped: number; total: number };
  recent: { file: string; status: string; processed_at: string; note_path: string; error: string }[];
  error?: string;
}

export const getWatcherStatus = async (): Promise<WatcherStatus | null> => {
  try {
    const res = await apiFetch("/api/watcher/status");
    if (res.ok) return await res.json();
  } catch { /* 백엔드 없음 */ }
  return null;
};

export const startWatcher = async (): Promise<{ ok: boolean; running: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/watcher/start", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, running: false, message: `시작 실패: ${e?.message || e}` };
  }
};

export const stopWatcher = async (): Promise<{ ok: boolean; running: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/watcher/stop", { method: "POST" });
    return await res.json();
  } catch (e: any) {
    return { ok: false, running: true, message: `중지 실패: ${e?.message || e}` };
  }
};

// ── 회의 비서 & Obsidian 진단 (assistant API) ─────────────────
export interface DiagnoseResult { ok: boolean; checks: { name: string; ok: boolean; detail: string }[] }
export const obsidianDiagnose = async (): Promise<DiagnoseResult> => {
  try {
    const res = await apiFetch("/api/assistant/obsidian-diagnose");
    return await res.json();
  } catch (e: any) {
    return { ok: false, checks: [{ name: "연결", ok: false, detail: `진단 실패: ${e?.message || e}` }] };
  }
};

export interface AssistantSummary {
  ok: boolean; message?: string; summary?: string;
  counts?: { meetings: number; conflicts: number; warnings: number; pending_merges: number };
  dashboard_path?: string;
}
export const assistantStatus = async (days = 7): Promise<AssistantSummary> => {
  try { return await (await apiFetch(`/api/assistant/status?days=${days}`)).json(); }
  catch (e: any) { return { ok: false, message: `현황 조회 실패: ${e?.message || e}` }; }
};
export const assistantSchedule = async (days = 14, writeDashboard = true): Promise<AssistantSummary> => {
  try {
    const res = await apiFetch("/api/assistant/schedule", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ days, write_dashboard: writeDashboard }),
    });
    return await res.json();
  } catch (e: any) { return { ok: false, message: `일정 갱신 실패: ${e?.message || e}` }; }
};

export interface PendingMerge { recording_title: string; recording_path: string; plan_title: string; matched_plan: string }
export const getMerges = async (): Promise<{ ok: boolean; message?: string; pending?: PendingMerge[] }> => {
  try { return await (await apiFetch("/api/assistant/merges")).json(); }
  catch (e: any) { return { ok: false, message: `병합 대기 조회 실패: ${e?.message || e}` }; }
};
export const doMerge = async (recordingPath: string, deleteRecording = false): Promise<{ ok: boolean; message: string }> => {
  try {
    const res = await apiFetch("/api/assistant/merge", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recording_path: recordingPath, delete_recording: deleteRecording }),
    });
    return await res.json();
  } catch (e: any) { return { ok: false, message: `병합 실패: ${e?.message || e}` }; }
};

export const vaultAudio = async (dryRun: boolean): Promise<{ ok: boolean; running?: boolean; count?: number; message: string }> => {
  try {
    const res = await apiFetch("/api/assistant/vault-audio", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dry_run: dryRun }),
    });
    return await res.json();
  } catch (e: any) { return { ok: false, message: `처리 실패: ${e?.message || e}` }; }
};
export const vaultAudioStatus = async (): Promise<{ running: boolean; done: number; message: string }> => {
  try { return await (await apiFetch("/api/assistant/vault-audio/status")).json(); }
  catch { return { running: false, done: 0, message: "" }; }
};

export interface PlanAutoStatus { running: boolean; vault: string; notes_researched: number; audio_processed: number; error?: string }
export const planStatus = async (): Promise<PlanAutoStatus | null> => {
  try { return await (await apiFetch("/api/watcher/plan/status")).json(); } catch { return null; }
};
export const planStart = async (): Promise<{ ok: boolean; running: boolean; message: string }> => {
  try { return await (await apiFetch("/api/watcher/plan/start", { method: "POST" })).json(); }
  catch (e: any) { return { ok: false, running: false, message: `시작 실패: ${e?.message || e}` }; }
};
export const planStop = async (): Promise<{ ok: boolean; running: boolean; message: string }> => {
  try { return await (await apiFetch("/api/watcher/plan/stop", { method: "POST" })).json(); }
  catch (e: any) { return { ok: false, running: true, message: `중지 실패: ${e?.message || e}` }; }
};

// 회의 준비 브리핑 생성. 참석자·추가노트를 검색·생성에 반영하고,
// 찾은 관련 볼트 노트(related)와 vault 연결 여부를 함께 반환한다.
export interface NoteRef { title: string; path: string; score?: number }
export interface PrepBriefResult {
  ok: boolean; brief?: string; message?: string;
  vault_connected?: boolean; related?: NoteRef[]; related_count?: number;
  open_actions?: number; recent_decisions?: number;
}
export const prepBrief = async (
  title: string, topic: string, opts?: { attendees?: string; notes?: string }
): Promise<PrepBriefResult> => {
  try {
    const res = await apiFetch("/api/prep-brief", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, topic, attendees: opts?.attendees || "", notes: opts?.notes || "" }),
    });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `브리핑 생성 실패: ${e?.message || e}` };
  }
};

// 생성된 회의 준비 브리핑을 세션으로 저장 → 대시보드에 표시.
export const savePrepBrief = async (
  data: { title: string; brief: string; topic?: string; date?: string; attendees?: string }
): Promise<{ ok: boolean; sessionId?: string; message?: string }> => {
  try {
    const res = await apiFetch("/api/prep-brief/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    return await res.json();
  } catch (e: any) {
    return { ok: false, message: `저장 실패: ${e?.message || e}` };
  }
};

// Profiles Management
const DEFAULT_PROFILES = [
  { name: "General Meeting", description: "Standard dual language", type: "meeting", language: "ko", translate: false, source: "builtin" },
];

export const getProfiles = async () => {
  const local = localStorage.getItem("APP_PROFILES");
  if (local) return JSON.parse(local);
  return DEFAULT_PROFILES;
};

export const createProfile = async (data: any) => {
  const profiles = await getProfiles();
  profiles.push({...data, source: "mobile"});
  localStorage.setItem("APP_PROFILES", JSON.stringify(profiles));
  return { success: true };
};

export const deleteProfile = async (name: string) => {
  let profiles = await getProfiles();
  profiles = profiles.filter((p: any) => p.name !== name);
  localStorage.setItem("APP_PROFILES", JSON.stringify(profiles));
  return { success: true };
};

// Sessions (IndexedDB via localforage)
export const getSessions = async (search?: string, type?: string) => {
  // 패키지(백엔드) 모드: 서버 SQLite 세션(폴더 워처·CLI·배치 산출 포함)을 로컬로
  // 미러링해 대시보드에 전체 세션이 보이게 한다 — 과거엔 IndexedDB만 나열해
  // 워처/CLI 세션이 영영 안 보였다. 없거나 상태가 바뀐 세션만 풀 미러링.
  if (await isPackagedMode()) {
    try {
      const base = getBackendUrl();
      const res = await fetch(`${base}/api/sessions`);
      if (res.ok) {
        const data = await res.json();
        const list: any[] = Array.isArray(data) ? data : (data?.sessions || []);
        for (const s of list) {
          if (!s?.id) continue;
          const local: any = await sessionsStore.getItem(s.id);
          if (!local || local.status !== s.status) {
            await mirrorServerSession(s.id);
          }
        }
      }
    } catch { /* 서버 미가용 시 로컬만 표시 */ }
  }

  const sessions: any[] = [];
  await sessionsStore.iterate((value) => {
    sessions.push(value);
  });
  
  // Sort by latest first
  let filtered = sessions.sort((a,b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());
  
  if (search) {
    const sl = search.toLowerCase();
    filtered = filtered.filter(s => s.title?.toLowerCase().includes(sl) || s.type?.toLowerCase().includes(sl));
  }
  if (type) filtered = filtered.filter(s => s.type === type);
  
  return filtered;
};

export const getSession = async (id: string): Promise<{ session: Session; segments: Segment[]; documents: Doc[] }> => {
  const session = await sessionsStore.getItem<Session>(id);
  const segments = await segmentsStore.getItem<Segment[]>(id) || [];
  const documents = await documentsStore.getItem<Doc[]>(id) || [];
  if (!session) throw new Error("Session not found in local IndexedDB");
  return { session, segments, documents };
};

// Wiki Knowledge Graph (읽기 전용) — web/backend/api/graph.py
// 참고: 이 앱은 대부분 IndexedDB(로컬)로 동작하고 web/backend REST를 호출하지 않지만,
// FastAPI로 함께 패키징되어 서빙되는 배포 모드(web/backend/app.py가 이 프론트를 정적 서빙)에서는
// 같은 오리진의 /api/* 가 실제로 존재한다. 백엔드가 없는(모바일 전용) 배포에서는 이 호출이
// 실패하며, 호출부에서 조용히 무시하고 그래프 탭을 숨기도록 처리한다.
export const getSessionGraph = async (sessionId: string): Promise<SessionGraph> => {
  const res = await apiFetch(`/api/graph/sessions/${sessionId}`);
  if (!res.ok) throw new Error(`Graph fetch failed (${res.status})`);
  return res.json();
};

// 전역 그래프 노드 검색/목록 (지식그래프 탐색 UI).
export const listGraphNodes = async (
  opts?: { type?: string; q?: string; limit?: number }
): Promise<GraphNode[]> => {
  const params = new URLSearchParams();
  if (opts?.type) params.set("type", opts.type);
  if (opts?.q) params.set("q", opts.q);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await apiFetch(`/api/graph/nodes${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Nodes fetch failed (${res.status})`);
  return res.json();
};

export const getNodeNeighbors = async (
  nodeId: string,
  opts?: { depth?: number; relationType?: string; limit?: number }
): Promise<GraphNeighbors> => {
  const params = new URLSearchParams();
  if (opts?.depth) params.set("depth", String(opts.depth));
  if (opts?.relationType) params.set("relation_type", opts.relationType);
  if (opts?.limit) params.set("limit", String(opts.limit));
  const qs = params.toString();
  const res = await apiFetch(`/api/graph/nodes/${nodeId}/neighbors${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`Neighbors fetch failed (${res.status})`);
  return res.json();
};

// Vault Wiki 질의응답 — web/backend/api/wiki.py (WikiQA 재사용, 서버 전용 기능).
// 필요한 것: 노트 폴더(.md)의 로컬 검색 인덱스 + 서버 LLM 키. Obsidian 앱/REST는
// 선택(있으면 검색 결과에 병합될 뿐, 없어도 폴더 인덱스만으로 동작). 서버 LLM 키가
// 필요해 브라우저 단독(모바일) 배포에는 대응 기능이 없다 — 호출 전 backendAvailable()로 확인.
export interface WikiAskResult {
  answer: string;
  sources: { title: string; path?: string; heading?: string; score?: number }[];
  has_conflict: boolean;
  unverified: boolean;
}

export const askWiki = async (question: string, maxNotes = 0): Promise<WikiAskResult> => {
  const base = getBackendUrl();
  const res = await fetch(`${base}/api/wiki/ask`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, max_notes: maxNotes }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => null);
    throw new Error(detail?.detail || `Wiki ask failed (${res.status})`);
  }
  return res.json();
};

export const getSessionStatus = async (id: string) => {
  const session: any = await sessionsStore.getItem(id);
  return { id, status: session?.status || "error" };
};

export const deleteSession = async (id: string) => {
  await sessionsStore.removeItem(id);
  await segmentsStore.removeItem(id);
  await documentsStore.removeItem(id);
  // 패키지 모드: 서버 DB에서도 삭제 — 로컬만 지우면 다음 미러링 때 되살아난다.
  if (await isPackagedMode()) {
    try { await fetch(`${getBackendUrl()}/api/sessions/${id}`, { method: "DELETE" }); } catch { /* 서버 미가용 무시 */ }
  }
  return { success: true };
};

export const clearSessions = async () => {
  await sessionsStore.clear();
  await segmentsStore.clear();
  await documentsStore.clear();
  if (await isPackagedMode()) {
    try { await fetch(`${getBackendUrl()}/api/sessions/clear`, { method: "POST" }); } catch { /* 서버 미가용 무시 */ }
  }
  return { success: true };
};

// Whisper API 단일 호출
const callWhisperAPI = async (file: File | Blob, apikey: string, topic?: string, language?: string): Promise<string> => {
  const fd = new FormData();
  fd.append("file", file, (file as File).name || "audio.webm");
  fd.append("model", "whisper-1");
  if (topic) fd.append("prompt", topic);
  if (language && language !== "auto") fd.append("language", language);

  const res = await fetch("https://api.openai.com/v1/audio/transcriptions", {
    method: "POST",
    headers: { "Authorization": `Bearer ${apikey}` },
    body: fd
  });
  if (!res.ok) {
    const errText = await res.text().catch(() => res.statusText);
    throw new Error(`Whisper API failed (${res.status}): ${errText}`);
  }
  const data = await res.json();
  if (data.error) throw new Error(data.error.message);
  return data.text || "";
};

const audioBufferToWavBlob = (buffer: AudioBuffer, startSec: number, endSec: number) => {
  const numOfChan = buffer.numberOfChannels;
  const sampleRate = buffer.sampleRate;
  const startOffset = Math.floor(startSec * sampleRate);
  let endOffset = Math.floor(endSec * sampleRate);
  if (endOffset > buffer.length) endOffset = buffer.length;
  const lengthInSamples = endOffset - startOffset;
  
  const bufferToEncode = new Float32Array(lengthInSamples * numOfChan);
  for (let i = 0; i < numOfChan; i++) {
    const channelData = buffer.getChannelData(i);
    let offset = i;
    for (let j = startOffset; j < endOffset; j++) {
      bufferToEncode[offset] = channelData[j];
      offset += numOfChan;
    }
  }
  
  const dataView = new DataView(new ArrayBuffer(44 + bufferToEncode.length * 2));
  const writeString = (view: DataView, offset: number, string: string) => {
    for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
  };
  
  writeString(dataView, 0, 'RIFF');
  dataView.setUint32(4, 36 + bufferToEncode.length * 2, true);
  writeString(dataView, 8, 'WAVE');
  writeString(dataView, 12, 'fmt ');
  dataView.setUint32(16, 16, true);
  dataView.setUint16(20, 1, true);
  dataView.setUint16(22, numOfChan, true);
  dataView.setUint32(24, sampleRate, true);
  dataView.setUint32(28, sampleRate * numOfChan * 2, true);
  dataView.setUint16(32, numOfChan * 2, true);
  dataView.setUint16(34, 16, true);
  writeString(dataView, 36, 'data');
  dataView.setUint32(40, bufferToEncode.length * 2, true);
  
  let offset = 44;
  for (let i = 0; i < bufferToEncode.length; i++) {
    let s = Math.max(-1, Math.min(1, bufferToEncode[i]));
    dataView.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
    offset += 2;
  }
  return new Blob([dataView], { type: 'audio/wav' });
};

// 큰 파일을 청크로 분할 (WebAudio API 기반 무결점 방식)
const splitFileIntoChunks = async (file: File, chunkMinutes = 10): Promise<Blob[]> => {
  const arrayBuffer = await file.arrayBuffer();
  const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
  
  const chunks: Blob[] = [];
  const chunkSizeSec = chunkMinutes * 60;
  for (let start = 0; start < audioBuffer.duration; start += chunkSizeSec) {
    chunks.push(audioBufferToWavBlob(audioBuffer, start, start + chunkSizeSec));
  }
  audioContext.close();
  return chunks;
};

// 서버 세션(SQLite)이 완료/에러가 될 때까지 폴링하며 IndexedDB로 미러링.
// 백그라운드 실행 — await 하지 않는다.
async function pollAndMirrorSession(sessionId: string, intervalMs = 3000, maxTries = 600) {
  for (let i = 0; i < maxTries; i++) {
    const ok = await mirrorServerSession(sessionId);
    if (ok) {
      const s: any = await sessionsStore.getItem(sessionId);
      if (s && (s.status === "completed" || s.status === "error")) return;
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

// File Upload via Direct Whisper API (자동 청크 분할 지원)
export const uploadFile = async (formData: FormData) => {
  // 패키지(백엔드) 모드: 서버 배치 파이프라인(/api/upload)으로 위임.
  // STT·회의록 생성이 서버에서 수행되어 OpenAI 키가 브라우저에 노출되지 않는다.
  if (await isPackagedMode()) {
    const res = await apiFetch("/api/upload", { method: "POST", body: formData });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new Error(d?.detail || `업로드 실패 (${res.status})`);
    }
    const data = await res.json();
    const sessionId = data.sessionId;
    void pollAndMirrorSession(sessionId); // 백그라운드 미러링
    return { sessionId, status: "processing" };
  }

  const file = formData.get("file") as File;
  const apikey = getApiKey();
  if (!apikey) throw new Error("OpenAI API Key is missing.");
  if (!file) throw new Error("No file provided.");

  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: formData.get("title") || file.name,
    type: formData.get("type") || "meeting",
    topic: formData.get("topic") as string || "",
    speakers: formData.get("speakers") as string || "",
    language: formData.get("language") as string || "",
    translate: formData.get("translate") === "true",
    status: "processing",
    created_at: new Date().toISOString(),
    source: "mobile"
  };
  await sessionsStore.setItem(sessionId, session);

  // Background processing
  (async () => {
    try {
      let fullText = "";

      if (file.size <= 24 * 1024 * 1024) {
        // 24MB 이하: 단일 호출
        fullText = await callWhisperAPI(file, apikey, session.topic, session.language);
      } else {
        // 24MB 초과: 안전한 WebAudio 기반 10분 단위 청크 분할 및 WAV 변환 후 순차 처리
        const chunks = await splitFileIntoChunks(file);
        const textParts: string[] = [];
        for (let i = 0; i < chunks.length; i++) {
          const chunkFile = new File([chunks[i]], `${file.name}_part${i + 1}.wav`, { type: 'audio/wav' });
          const partText = await callWhisperAPI(chunkFile, apikey, session.topic, session.language);
          textParts.push(partText);
        }
        fullText = textParts.join("\n\n");
      }

      const segments = [{ start: 0, end: 0, text: fullText || "(No speech detected)", speaker: "Audio", translatedText: "" }];
      await segmentsStore.setItem(sessionId, segments);

      await generateSummaryForSession(sessionId);
    } catch (e: any) {
      console.error("Audio processing failed", e);
      session.status = "error";
      await sessionsStore.setItem(sessionId, session);
    }
  })();

  return { sessionId, status: "processing" };
};

// Text Input Direct Processing
export const processTextInput = async (text: string, metadata: any) => {
  // 패키지(백엔드) 모드: 서버가 텍스트→회의록 생성(키 미노출).
  if (await isPackagedMode()) {
    const res = await apiFetch("/api/process-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, title: metadata.title, topic: metadata.topic, type: metadata.type }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => null);
      throw new Error(d?.detail || `처리 실패 (${res.status})`);
    }
    const data = await res.json();
    void pollAndMirrorSession(data.sessionId);
    return { sessionId: data.sessionId, status: "processing" };
  }
  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: metadata.title || "Text Document",
    type: metadata.type || "meeting",
    topic: metadata.topic || "",
    translate: metadata.translate || false,
    language: metadata.language || "",
    status: "processing",
    created_at: new Date().toISOString(),
    source: "mobile"
  };
  
  const segments = [{ start: 0, end: 0, speaker: "Document", text, translatedText: "" }];
  await sessionsStore.setItem(sessionId, session);
  await segmentsStore.setItem(sessionId, segments);
  
  // Start background summary
  generateSummaryForSession(sessionId);
  return { sessionId, status: "processing" };
};

// ── 백엔드 연동 (FastAPI가 함께 떠 있는 배포 모드) ─────────────────────
// 백엔드가 있으면 녹음 오디오를 서버 /ws/realtime로 보내 STT·실시간 vault 검색
// (related_notes)·회의록 생성을 서버가 수행한다 — OpenAI API 키가 브라우저에
// 노출되지 않는다. 모바일 단독 배포(백엔드 없음)는 기존 직접 OpenAI 연결로 폴백.

export const getBackendUrl = () => localStorage.getItem("BACKEND_URL") || "";
export const setBackendUrl = (url: string) => {
  const clean = (url || "").trim().replace(/\/+$/, "");
  if (clean) localStorage.setItem("BACKEND_URL", clean);
  else localStorage.removeItem("BACKEND_URL");
  resetPackagedMode();  // 다음 isPackagedMode() 호출 시 새 주소로 재감지
};

export async function backendAvailable(): Promise<boolean> {
  const base = getBackendUrl();
  try {
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 1500);
    const res = await fetch(`${base}/api/health`, { signal: ctrl.signal });
    clearTimeout(t);
    return res.ok;
  } catch {
    return false;
  }
}

export function createBackendRealtimeWS(): WebSocket {
  const base = getBackendUrl();
  let wsUrl: string;
  if (base) {
    wsUrl = base.replace(/^http/, "ws") + "/ws/realtime";
  } else {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    wsUrl = `${proto}//${location.host}/ws/realtime`;
  }
  return new WebSocket(wsUrl);
}

// 서버 세션(SQLite)을 IndexedDB로 미러링 — 기존 Dashboard/SessionDetail이
// 무수정으로 서버 생성 세션을 표시할 수 있게 한다.
export async function mirrorServerSession(sessionId: string): Promise<boolean> {
  const base = getBackendUrl();
  try {
    // 타임아웃 필수: 서버가 연결만 받고 응답을 안 주면 이 fetch가 무한 대기하고,
    // 호출부의 .finally(→ 화면 이동)가 영영 안 돌아 녹음 후 멈춤이 된다.
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), 8000);
    const res = await fetch(`${base}/api/sessions/${sessionId}`, { signal: ctrl.signal });
    clearTimeout(t);
    if (!res.ok) return false;
    const data = await res.json();
    if (data.session) await sessionsStore.setItem(sessionId, data.session);
    if (data.segments) {
      await segmentsStore.setItem(sessionId, data.segments.map((s: any) => ({
        ...s,
        // RealtimeSegment 스타일 별칭 (일부 뷰가 translatedText를 참조)
        translatedText: s.translated_text || "",
        start: s.start_time ?? 0,
        end: s.end_time ?? 0,
      })));
    }
    if (data.documents) await documentsStore.setItem(sessionId, data.documents);
    return true;
  } catch {
    return false;
  }
}

// WebSocket connector for OpenAI Realtime API
export function createRealtimeWS(): WebSocket {
  const apiKey = getApiKey();
  if (!apiKey) throw new Error("OpenAI API Key is missing. Please configure it in Settings.");

  const url = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01";
  const ws = new WebSocket(url, [
    "realtime",
    `openai-insecure-api-key.${apiKey}`,
    "openai-beta.realtime-v1"
  ]);

  // 모바일 WebView 연결 타임아웃 감지
  const timeout = setTimeout(() => {
    if (ws.readyState !== WebSocket.OPEN) {
      console.error("[RealtimeWS] Connection timeout (10s). readyState:", ws.readyState);
      ws.close();
    }
  }, 10000);

  ws.addEventListener('open', () => {
    clearTimeout(timeout);
    console.log("[RealtimeWS] Connected successfully");
  });

  ws.addEventListener('error', (e) => {
    clearTimeout(timeout);
    console.error("[RealtimeWS] WebSocket error:", e);
  });

  return ws;
}

export const saveCompleteSession = async (sessionData: any, segments: any[]) => {
  const sessionId = crypto.randomUUID();
  const session = {
    id: sessionId,
    title: sessionData.title || "Realtime Session",
    type: sessionData.type || "meeting",
    topic: sessionData.topic || "",
    status: "processing", // initial status
    created_at: new Date().toISOString(),
    source: "mobile",
    duration_sec: sessionData.duration || 0,
    translate: sessionData.translate || false
  };
  await sessionsStore.setItem(sessionId, session);
  await segmentsStore.setItem(sessionId, segments);
  return sessionId;
};

// ChatGPT API Call with Fallback Logic
const callOpenAIWithFallback = async (prompt: string, apikey: string, primaryModel: string, fallbackModel = "gpt-4o-mini") => {
   const doFetch = async (model: string) => {
     const res = await fetch("https://api.openai.com/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${apikey}` },
        body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }] })
     });
     if (!res.ok) {
       const errText = await res.text().catch(() => res.statusText);
       throw new Error(`OpenAI API failed (${res.status}): ${errText}`);
     }
     const data = await res.json();
     if (data.error) throw new Error(data.error.message || JSON.stringify(data.error));
     return data.choices[0].message.content;
   };

   try {
     return await doFetch(primaryModel);
   } catch (e: any) {
     console.warn(`[API] Primary model ${primaryModel} failed: ${e.message}. Trying fallback ${fallbackModel}...`);
     if (primaryModel !== fallbackModel) {
       return await doFetch(fallbackModel);
     }
     throw e;
   }
};

// Client-side Session Document Generation
export const generateSummaryForSession = async (sessionId: string, userNotes?: string) => {
   // 패키지 모드: 서버가 기존 전사를 재사용해 노트를 반영, 회의록을 재생성.
   if (await isPackagedMode()) {
     const res = await apiFetch(`/api/sessions/${sessionId}/regenerate`, {
       method: "POST",
       headers: { "Content-Type": "application/json" },
       body: JSON.stringify({ notes: userNotes || "" }),
     });
     if (!res.ok) {
       const d = await res.json().catch(() => null);
       throw new Error(d?.detail || `재생성 실패 (${res.status})`);
     }
     void pollAndMirrorSession(sessionId);
     return;
   }
   const session: any = await sessionsStore.getItem(sessionId);
   const segments: any = await segmentsStore.getItem(sessionId) || [];
   if (!session || segments.length === 0) return;

   try {
     session.status = "processing";
     await sessionsStore.setItem(sessionId, session);

     const config = await getConfig();
     const apikey = getApiKey();
     if (!apikey) throw new Error("No API Key");
     
     const text = segments.map((s:any) => `[${s.speaker || 'Speaker'}] ${s.text} ${s.translatedText ? '(' + s.translatedText + ')' : ''}`).join('\n');
     
     let noteContext = session.topic ? `\n\nContext/Topic:\n${session.topic}` : "";
     if (session.speakers) noteContext += `\nParticipants: ${session.speakers}`;
     if (userNotes) noteContext += `\n\nUser Notes:\n${userNotes}`;
     
     const prompt1 = `You are an expert meeting assistant. Summarize the following transcript in Korean. Use markdown formatting with clear headings like "세션 요약" and "주요 내용".${noteContext}\n\nTranscript:\n${text}`;
     const minutesContent = await callOpenAIWithFallback(prompt1, apikey, config.models?.gpt_model || "gpt-4o-mini");
     
     const prompt2 = `Extract action items from the following meeting transcript in Korean. Format as a markdown list with checkboxes.${noteContext}\n\nTranscript:\n${text}`;
     const actionsContent = await callOpenAIWithFallback(prompt2, apikey, config.models?.gpt_model || "gpt-4o-mini");

     const documents = [
       { type: "minutes", content: minutesContent, format: "md" },
       { type: "actions", content: actionsContent, format: "md" },
       { type: "summary", content: minutesContent, format: "md" } // duplicate for summary tab fallback
     ];
     await documentsStore.setItem(sessionId, documents);
     
     session.status = "completed";
     await sessionsStore.setItem(sessionId, session);
   } catch (e) {
     console.error("Summary generation failed", e);
     session.status = "error";
     await sessionsStore.setItem(sessionId, session);
   }
};

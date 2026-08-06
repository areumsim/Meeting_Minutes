import React, { useState, useEffect, useRef, useCallback } from "react";
import { Mic, Square, Play, Pause, ChevronDown } from "lucide-react";
import {
  createRealtimeWS, backendAvailable, createBackendRealtimeWS, mirrorServerSession,
  getCostRates, getConfig, type CostRates,
} from "../lib/api";
import { MODE_PRESETS, type Facilitation, type FacilitationStatus, type RealtimeSegment } from "../lib/types";
import ModePanel from "../screens/create/ModePanel";
import PersonaPanel from "../ui/PersonaPanel";
import Inspector from "../ui/Inspector";
import { Button } from "../ui/Button";
import { Banner } from "../ui/Banner";
import { StatusPill } from "../ui/StatusPill";
import { Field, Select, Textarea, TextField } from "../ui/Field";
import RecordingHeader from "../screens/recording/RecordingHeader";
import TranscriptPanel, { type ViewMode } from "../screens/recording/TranscriptPanel";
import RelatedNotesTab from "../screens/recording/RelatedNotesTab";
import { KeepAwake } from '@capacitor-community/keep-awake';
import { Haptics, ImpactStyle, NotificationType } from '@capacitor/haptics';

interface RelatedNote {
  filename: string;
  title: string;
  score: number;
  snippet?: string;
  heading?: string;         // 매칭된 섹션(헤딩) — 근거 추적용
  sectionPath?: string;     // "노트 › 헤딩"
  sourceType?: string;      // note(📄) | paper(🎓) | web(🌐)
  foundBy?: string;         // section | note | web
  segmentText?: string;     // 이 노트를 띄운 발화
  rankScore?: number;
}

// 실시간 검색 백엔드 상태(FR-1) — 왜 안 뜨는지 조용히 알려주는 배지용
interface WikiSearchStatus {
  enabled: boolean;
  gate: boolean;
  backend: string;
  reason: string;
  reasonText: string;
}

// 출처유형 → 아이콘. 규약 정본은 source_type 을 만드는 파이썬 쪽
// `wiki_core/realtime_search.py: SOURCE_ICON` — 값을 바꿀 땐 그쪽을 먼저 고친다.
const SOURCE_ICON: Record<string, string> = { paper: "🎓", web: "🌐", note: "📄" };

// 내부(📄/🎓)를 웹(🌐)보다 앞줄에 두고, 그 안에서는 랭크 점수 순으로 정렬한다.
function sortRelated(notes: RelatedNote[]): RelatedNote[] {
  const weight = (n: RelatedNote) =>
    n.sourceType === "web" ? 2 : n.sourceType === "paper" ? 0 : 1;
  return [...notes].sort((a, b) =>
    weight(a) - weight(b) || (b.rankScore ?? b.score ?? 0) - (a.rankScore ?? a.score ?? 0));
}

// 음성 인식(STT) 모델 선택지 — config_schema의 models.stt 옵션과 동일하게 유지.
// 녹음 화면에서 '이번 녹음만' 임시로 바꿀 수 있게 노출한다(설정 기본값은 그대로).
// 라벨은 **평문**이다 — 모델 ID 는 화면에 노출하지 않는다(PRD §10 부록 A). 값은 그대로라
// 서버 계약(config.models.stt)은 바뀌지 않는다.
const STT_OPTIONS: { value: string; label: string }[] = [
  { value: "gpt-4o-mini-transcribe", label: "저렴·빠름 (기본)" },
  { value: "gpt-4o-transcribe", label: "고정확" },
  { value: "gpt-4o-transcribe-diarize", label: "화자 구분 (실시간에서는 자동 전환)" },
  { value: "whisper-1", label: "구형·안정" },
];

// ── 소리를 어떻게 잡을지 ─────────────────────────────────────────────
// 상황에 따라 마이크 제약 조건이 **정반대**로 필요하다. 근접 발화(헤드셋)에는 에코 취소가
// 이롭지만, 회의실 TV·스피커폰에서 나오는 상대 목소리에는 그 에코 취소가 바로 그 소리를
// 지워버린다. 그래서 체크박스 조합(모순 조합이 생긴다)이 아니라 **상황 이름 3택**으로 고르게
// 하고, 제약 조건은 아래 두 기준값 중 하나를 그대로 쓴다.
type CaptureMode = "mic" | "mic+system" | "room";

/** 근접 발화용 — 에코 취소 on, 원본 레벨 유지(자동 게인 off). 기존 기본값. */
const NEAR_FIELD_AUDIO: MediaTrackConstraints = {
  echoCancellation: { ideal: true },
  autoGainControl: { ideal: false },
};
/** 원거리(스피커·TV)용 — 근접 기준값 둘을 뒤집는다. 에코 취소는 스피커에서 나는 소리를
 *  '되돌아온 내 소리'로 보고 지우고, 자동 게인을 끈 탓에 작은 소리가 올라오지도 않는다. */
const FAR_FIELD_AUDIO: MediaTrackConstraints = {
  echoCancellation: { ideal: false },
  autoGainControl: { ideal: true },
};

const CAPTURE_MODES: {
  value: CaptureMode; label: string; desc: string;
  audio: MediaTrackConstraints; system: boolean;
}[] = [
  { value: "mic", label: "내 마이크만",
    desc: "대면 회의·헤드셋 발화",
    audio: NEAR_FIELD_AUDIO, system: false },
  { value: "mic+system", label: "내 마이크 + 이 PC 소리",
    // 앱 이름을 여럿 적는 이유: 시스템 오디오는 **OS 레벨 루프백**이라 어느 회의 앱이든
    // 똑같이 잡히는데, "Teams 등"만 적혀 있으니 줌 사용자가 자기 경우인지 물어봤다.
    desc: "Zoom·Teams·Webex·Meet 등 온라인 회의 — 헤드셋을 써도 상대방 목소리가 함께 녹음됩니다",
    audio: NEAR_FIELD_AUDIO, system: true },
  { value: "room", label: "회의실 마이크 (멀리 있는 소리)",
    desc: "TV·스피커폰으로 상대 목소리가 나오는 회의실",
    audio: FAR_FIELD_AUDIO, system: false },
];

// 이 PC 소리 캡처는 브라우저 화면공유 API를 쓴다 — iOS(Capacitor) WebView엔 없다.
const HAS_DISPLAY_MEDIA = typeof navigator !== "undefined"
  && typeof (navigator.mediaDevices as any)?.getDisplayMedia === "function";

function loadCaptureMode(): CaptureMode {
  try {
    const saved = localStorage.getItem("CAPTURE_MODE") as CaptureMode | null;
    const def = CAPTURE_MODES.find((m) => m.value === saved);
    // 저장된 선택이 이 기기에서 불가능하면(예: iOS에서 'mic+system') 기본으로 되돌린다.
    if (def && (!def.system || HAS_DISPLAY_MEDIA)) return def.value;
  } catch { /* ignore */ }
  return "mic";
}

export default function Recorder({ onComplete, onExit }: { onComplete: (id: string) => void; onExit?: () => void }) {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [duration, setDuration] = useState(0);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [modeNum, setModeNum] = useState(1);  // 기본: 한국어 회의 (MODE_PRESETS[1])
  const [speakers, setSpeakers] = useState("");
  const [isSettingsCollapsed, setIsSettingsCollapsed] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState<RealtimeSegment[]>([]);
  const [status, setStatus] = useState<string>("idle");
  const [volume, setVolume] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [wsStatus, setWsStatus] = useState<string>("");
  // 전사 표시 토글(FR-REC-4) — 서버 동작과 무관한 화면 상태다.
  const [viewMode, setViewMode] = useState<ViewMode>("both");
  // 인스펙터 탭(FR-REC-6) — 관련 노트 / 진행 도우미. 한 번에 하나만 본다.
  const [inspectorTab, setInspectorTab] = useState<"notes" | "personas">("notes");
  // WebSocket 실시간 전사가 안 돼 HTTP 청크 방식으로 자동 전환됐는지 여부.
  // (에러가 아니라 정상 폴백이므로 사용자에게 안내로만 노출한다.)
  const [httpFallback, setHttpFallback] = useState(false);
  // 백엔드 모드 — 서버가 STT/실시간 vault 검색/회의록 생성 수행 (API 키 미노출)
  const [relatedNotes, setRelatedNotes] = useState<RelatedNote[]>([]);
  const [wikiStatus, setWikiStatus] = useState<WikiSearchStatus | null>(null);
  // 관련 노트 근거(점수·섹션경로·snippet·발화) 펼침 — 사용자가 눌렀을 때만 펼친다
  // (자동 갱신으로는 절대 레이아웃이 움직이지 않게 하기 위함, FR-10)
  const [wikiExpanded, setWikiExpanded] = useState(false);
  // 관련 노트 '이번 회의 끔' — 페르소나 mute 와 같은 계약(서버 검색·과금까지 정지).
  // ref 를 함께 두는 이유도 같다: WS onmessage 는 렌더와 무관한 클로저라 state 만으로는
  // 끈 직후 도착한 결과를 목록에 다시 넣는다.
  const [wikiMuted, setWikiMuted] = useState(false);
  const wikiMutedRef = useRef(false);
  // 서버 경유 녹음인지 (관련 노트 바 표시 조건 — 단독 OpenAI 경로는 vault 검색 없음)
  const [backendMode, setBackendMode] = useState(false);
  // 회의 진행 페르소나(facilitation) — 기본 꺼짐이라 서버가 보내지 않으면 레인 자체가
  // 렌더되지 않는다. 카드는 최근 것이 앞(가로 스크롤 왼쪽)에 오도록 앞에 붙인다.
  const [interventions, setInterventions] = useState<Facilitation[]>([]);
  const [facStatus, setFacStatus] = useState<FacilitationStatus | null>(null);
  const [facPending, setFacPending] = useState(0);
  // '이번 회의 끔' — 세션 중 끄면 그 세션에서는 다시 켜지 않는다(PRD §4·§19.4).
  // ref 를 함께 두는 이유: WS onmessage 는 렌더와 무관하게 도는 클로저라 state 만으로는
  // 끈 직후 도착한 카드를 놓친다(관련 노트 바의 backendModeRef 와 같은 이유).
  const [facMuted, setFacMuted] = useState(false);
  const facMutedRef = useRef(false);
  // 이번 회의에서 실제 발생한 개입 비용 합계(USD) — 카드를 닫아도 줄지 않는다.
  // 개입은 시간 비례가 아니라 건수 기반이어서 분당 요율로는 표현할 수 없다(서버가
  // 각 이벤트에 costUsd 를 실어 보낸다).
  const [facCostUsd, setFacCostUsd] = useState(0);
  // 이미 받은 개입 id — 비용 중복 합산 방지(카드를 닫아도 여기서는 지우지 않는다).
  const facSeenIdsRef = useRef<Set<string>>(new Set());
  // 중간 요약(🧾 summarizer)이 이 녹음에서 도는지 — 서버가 시작 시 실효값으로 알려준다
  // (참견도 클램프는 코어만 알기 때문에 프런트가 config 로 재계산하지 않는다).
  const [facBriefOn, setFacBriefOn] = useState(false);
  const [facBriefBusy, setFacBriefBusy] = useState(false);
  // 발화 점프로 강조한 전사 줄(start 초). 잠깐 테두리를 주고 지운다.
  const [flashStart, setFlashStart] = useState<number | null>(null);
  const flashTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [costRates, setCostRates] = useState<CostRates | null>(null);
  // 이번 녹음에 쓸 STT 모델(설정 기본값이 자동 채워지며, 이 값만 바꿔도 설정은 안 바뀜)
  const [sttModel, setSttModel] = useState<string>("");
  const [captureMode, setCaptureMode] = useState<CaptureMode>(loadCaptureMode);
  // 이 PC 소리 캡처가 기대대로 안 됐을 때의 안내(취소·오디오 미공유·도중 중단).
  // 조용히 마이크만 녹음되면 나중에야 상대 발언이 통째로 빈 걸 알게 된다.
  const [captureNote, setCaptureNote] = useState("");
  // 이 PC 소리가 '지금 실제로' 들어오고 있는지 — 고른 값이 아니라 성사된 상태다.
  const [systemAudioOn, setSystemAudioOn] = useState(false);
  const backendModeRef = useRef(false);
  const provisionalIdxRef = useRef<Record<string, number>>({});
  const completionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const serverSessionIdRef = useRef<string | null>(null);
  // 백엔드 모드에서 오디오 캡처를 한 번만 시작하도록 가드(WS→HTTP 폴백 시 ready·fallback_http
  // 이벤트가 연달아 와도 캡처를 중복 시작하지 않게 함 — 같은 스트림을 서버가 이어서 읽는다).
  const captureStartedRef = useRef(false);

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const silentOscRef = useRef<OscillatorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const displayStreamRef = useRef<MediaStream | null>(null);  // 이 PC 소리(화면공유 오디오)
  const analyserRef = useRef<AnalyserNode | null>(null);
  const animFrameRef = useRef<number | null>(null);
  const isPausedRef = useRef(false);

  const stopRecordingRef = useRef<any>(null);
  const stateRef = useRef({ title, topic, duration, modeNum });
  useEffect(() => {
    stateRef.current = { title, topic, duration, modeNum };
  }, [title, topic, duration, modeNum]);

  useEffect(() => {
    return () => {
      // Auto-save on accidental unmount — 백엔드 모드는 서버가 수신분을 저장·처리하므로
      // 로컬 IndexedDB에 문서 없는 중복 'processing' 세션을 만들지 않는다.
      if ((window as any).isRecordingActive && !backendModeRef.current) {
        const finalTranscript = transcriptRef.current;
        const current = stateRef.current;
        const preset = MODE_PRESETS[current.modeNum] || MODE_PRESETS[2];
        import("../lib/api").then(api => {
           api.saveCompleteSession({ title: current.title, topic: current.topic, type: preset.type, duration: current.duration, translate: preset.translate }, finalTranscript).catch(()=>{});
        }).catch(()=>{});
      }
      (window as any).isRecordingActive = false;
      delete (window as any).stopActiveRecording;
      if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
      if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
      stopAll();
    };
  }, []);

  // 실시간 비용 요율 로드(백엔드 모드) — 러닝 비용 추정용
  useEffect(() => { getCostRates().then((r) => r && setCostRates(r)); }, []);

  // 설정의 기본 STT 모델을 불러와 드롭다운 기본값으로 표시(수정 가능 — 이번 녹음에만 적용)
  useEffect(() => {
    getConfig().then((c) => { const m = c?.models?.stt; if (m) setSttModel(String(m)); }).catch(() => {});
  }, []);

  const transcriptRef = useRef<RealtimeSegment[]>([]);
  const transcriptPanelRef = useRef<HTMLDivElement>(null);
  // 맨 아래로 간주하는 여유(px) — 아래 효과와 onScroll 이 **같은 기준**을 써야 한다.
  const NEAR_BOTTOM_PX = 120;
  // 위로 스크롤해 이전 내용을 읽는 중인지 + 그 사이 새로 쌓인 줄 수(맨 아래로 버튼용).
  const [followLatest, setFollowLatest] = useState(true);
  const [unseenCount, setUnseenCount] = useState(0);
  // 자동 따라가기 여부의 단일 진실 — "사용자가 마지막으로 스크롤한 위치"(= 의도)다.
  // 새 내용이 붙은 **뒤에** 기하를 재면 안 된다: 한 번의 업데이트가 NEAR_BOTTOM_PX 보다
  // 크면(revise 가 25초 창의 조각을 문장으로 한꺼번에 교체할 때, 한국어 긴 발화가 여러
  // 줄로 감길 때) 사용자가 맨 아래에 있었는데도 '멀어졌다'고 오판해 따라가기가 멈췄고,
  // followLatest 는 그대로 true 라서 [최신 전사로] 버튼조차 뜨지 않아 패널이 얼었다.
  const atBottomRef = useRef(true);
  useEffect(() => {
    const prevLen = transcriptRef.current.length;
    transcriptRef.current = liveTranscript;
    const panel = transcriptPanelRef.current;
    if (atBottomRef.current) {
      // 패널 안에서만 스크롤한다. scrollIntoView 는 document 까지 포함해 모든 스크롤
      // 조상을 감아서, 페이지가 스크롤된 상태면 토큰마다 화면 전체가 튀었다.
      if (panel) panel.scrollTop = panel.scrollHeight;
      setFollowLatest(true);
      setUnseenCount((c) => (c ? 0 : c));   // 같은 값이면 React 가 리렌더를 건너뛴다
    } else if (liveTranscript.length > prevLen) {
      setUnseenCount((c) => c + (liveTranscript.length - prevLen));
    }
  }, [liveTranscript]);

  const jumpToLatest = () => {
    const panel = transcriptPanelRef.current;
    if (panel) panel.scrollTo({ top: panel.scrollHeight, behavior: "smooth" });
    atBottomRef.current = true;
    setUnseenCount(0);
    setFollowLatest(true);
  };

  // ── 회의 진행 페르소나 상호작용 (PRD §19.4) ────────────────────────────
  // 공통 규칙: 어느 동작도 새 LLM 호출을 만들지 않는다. 회의 중 버튼 한 번이 과금을
  // 일으키면 사용자가 비용을 예측할 수 없다.

  /** 발화 점프 — **전사 패널 안에서만** 스크롤한다(녹음 중 외부 이동 금지 정책). */
  const jumpToSpan = (span: { t0: number; t1: number }) => {
    const panel = transcriptPanelRef.current;
    if (!panel) return;
    // 근거 구간 시작 시각 이하의 마지막 줄 = 그 발화가 있는 줄. 전사 줄은 화면에
    // 최근 MAX_VISIBLE_LINES 개만 그리므로 밀려난 구간은 못 찾을 수 있다.
    const rows = Array.from(
      panel.querySelectorAll<HTMLElement>("[data-seg-start]"));
    let target: HTMLElement | null = null;
    for (const el of rows) {
      const t = Number(el.dataset.segStart);
      if (Number.isFinite(t) && t >= 0 && t <= span.t0 + 0.5) target = el;
    }
    if (!target) {
      setFacStatus({
        kind: "jump",
        message: "그 발화는 전사 화면에서 밀려났습니다 — 종료 후 전사에서 볼 수 있습니다",
      });
      return;
    }
    // scrollIntoView 는 페이지까지 함께 스크롤해 화면이 튄다(위 자동 스크롤 주석과
    // 같은 이유) — 패널 좌표계로 직접 계산한다.
    panel.scrollTo({
      top: Math.max(0, target.offsetTop - panel.offsetTop - 24),
      behavior: "smooth",
    });
    // 사용자가 과거를 보고 있는 동안 새 전사가 오면 다시 아래로 끌려간다 → 따라가기를
    // 끊고, 돌아갈 [최신 전사로] 버튼을 띄운다.
    atBottomRef.current = false;
    setFollowLatest(false);
    setFlashStart(Number(target.dataset.segStart));
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
    flashTimerRef.current = setTimeout(() => setFlashStart(null), 2500);
  };
  useEffect(() => () => {
    if (flashTimerRef.current) clearTimeout(flashTimerRef.current);
  }, []);

  /** [지금 점검] — 서버가 모아둔 참견도 2 개입을 방출한다(추가 과금 없음). */
  const facCheckNow = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type: "facilitation_check" })); } catch (e) {}
  };

  /**
   * [지금 정리] — 주기를 기다리지 않고 중간 요약 1회.
   * [지금 점검]과 달리 **새 LLM 호출이 생긴다**(요약 1회). 서버가 연타·내용 없음·한도를
   * 판정해 사유를 돌려주므로 프런트는 중복 가드만 둔다.
   */
  const facBriefNow = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || facBriefBusy) return;
    setFacBriefBusy(true);
    try { ws.send(JSON.stringify({ type: "facilitation_brief_now" })); }
    catch (e) { setFacBriefBusy(false); }
  };

  /**
   * '이번 회의 끔'을 **서버에 알린다** — 두 끄기(페르소나·관련 노트)의 공통 부분.
   *
   * 이게 본체다. 프런트에서 목록만 숨기면 서버는 회의 끝까지 생성·검색을 계속해
   * 과금하고, 그 금액은 러닝 미터에 잡히지 않아 표시 금액이 실제보다 작아진다.
   * 로컬 상태 정리는 이미 도착한 것을 치우는 것뿐이라 호출부에 남긴다(끄는 대상이
   * 서로 다르다).
   */
  const sendMute = (type: "facilitation_mute" | "related_notes_mute") => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN) return;
    try { ws.send(JSON.stringify({ type })); } catch (e) {}
  };

  /** 페르소나 [이번 회의 끔] — 설정은 그대로 두고 이 세션의 개입을 멈춘다(§19.4). */
  const facMute = () => {
    facMutedRef.current = true;
    setFacMuted(true);
    setInterventions([]);
    setFacPending(0);
    setFacStatus(null);
    sendMute("facilitation_mute");
  };

  /**
   * 관련 노트 [이번 회의 끔] — 페르소나 끄기와 **같은 계약**이다.
   *
   * 서버가 검색을 멈추는 것이 본체다. 목록만 숨기면 볼트 검색(쿼리 임베딩)과 웹 보완
   * (검색 1,000회당 $10)이 회의 끝까지 계속 나간다 — 아무도 안 보는 결과에 돈을 쓴다.
   * 이미 찾은 노트는 회의록에 남는다(이미 지불한 것을 지우면 잃기만 한다).
   */
  const wikiMute = () => {
    wikiMutedRef.current = true;
    setWikiMuted(true);
    setRelatedNotes([]);
    setWikiExpanded(false);
    sendMute("related_notes_mute");
  };

  /**
   * 관련 노트 바를 새 녹음 기준으로 되돌린다 — `resetFacilitation` 과 같은 역할.
   *
   * 끄기는 **그 회의에서만** 유효하므로 mute 상태도 함께 지운다. 종전엔 시작 경로에만
   * 인라인으로 있었고 취소·이탈 경로에는 없어서, 껐다가 취소하면 대기 화면에
   * "이번 회의 끔" 초록 바가 남아 있었다(바 표시 조건에 muted 가 들어간다).
   */
  const resetRelatedNotes = () => {
    wikiMutedRef.current = false;
    setWikiMuted(false);
    setRelatedNotes([]);
    setWikiStatus(null);
    setWikiExpanded(false);
  };

  /**
   * 확인(✓)·닫기(✕) — 카드를 화면에서 빼고 **그 판단을 서버 관찰 로그에 남긴다**.
   * 회의 중 누른 이 버튼이 오탐률 실측(PRD §15)의 사람 라벨이 된다 — 종료 후 별도
   * 라벨링을 요구하면 데이터가 모이지 않는다. 비용 합계는 줄지 않는다(이미 발생분).
   */
  const facFeedback = (id: string, label: "ack" | "dismiss") => {
    const item = interventions.find(p => p.id === id);
    setInterventions(prev => prev.filter(p => p.id !== id));
    const ws = wsRef.current;
    if (!item?.spanHash || !ws || ws.readyState !== WebSocket.OPEN) return;
    try {
      ws.send(JSON.stringify({
        type: "facilitation_feedback",
        persona: item.persona, spanHash: item.spanHash, label,
      }));
    } catch (e) {}
  };

  const resetFacilitation = () => {
    facMutedRef.current = false;
    facSeenIdsRef.current = new Set();
    setFacMuted(false);
    setInterventions([]);
    setFacStatus(null);
    setFacPending(0);
    setFacCostUsd(0);
    setFacBriefOn(false);        // 서버가 새 녹음 시작 시 다시 알려준다(ready)
    setFacBriefBusy(false);
  };

  // 스크롤 위치로 '최신 따라가기' 상태 갱신 — 사용자가 위를 읽는 동안엔 배지를 띄운다.
  // (위 효과의 자동 스크롤도 scroll 이벤트를 발생시켜 여기서 true 로 재확인된다.)
  const onTranscriptScroll = () => {
    const panel = transcriptPanelRef.current;
    if (!panel) return;
    const atBottom =
      panel.scrollHeight - panel.scrollTop - panel.clientHeight < NEAR_BOTTOM_PX;
    atBottomRef.current = atBottom;
    setFollowLatest(atBottom);
    if (atBottom) setUnseenCount((c) => (c ? 0 : c));
  };

  // 마이크 입력 감지: volume(0~255, analyser 평균)에 저임계값 적용.
  // 무음 노이즈 플로어(≈0~2)를 넘으면 '소리 감지 중'으로 본다.
  // volume 은 매 프레임 갱신되므로 임계값을 그대로 쓰면 발화 중의 자연스러운 휴지마다
  // 칩이 '소리 감지 중'↔'무음'으로 초당 여러 번 뒤집혀 "마이크가 끊겼나?"로 읽힌다.
  // → 마지막 감지 후 SOUND_HOLD_MS 동안 유지하는 히스테리시스를 둔다.
  const SOUND_THRESHOLD = 4;
  const SOUND_HOLD_MS = 1200;
  const [soundActive, setSoundActive] = useState(false);
  const soundActiveRef = useRef(false);
  const soundOffTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const clearSoundOffTimer = () => {
    if (soundOffTimerRef.current) {
      clearTimeout(soundOffTimerRef.current);
      soundOffTimerRef.current = null;
    }
  };
  const applySoundActive = (v: boolean) => {
    soundActiveRef.current = v;
    setSoundActive(v);          // 같은 값이면 React 가 리렌더를 건너뛴다
  };
  useEffect(() => {
    if (!isRecording || isPaused) {
      clearSoundOffTimer();
      applySoundActive(false);
      return;
    }
    if (volume > SOUND_THRESHOLD) {
      clearSoundOffTimer();
      applySoundActive(true);
      return;
    }
    // 이미 '무음'이면 타이머를 새로 걸지 않는다(1.2초마다 헛 타이머가 도는 것 방지).
    if (soundActiveRef.current && !soundOffTimerRef.current) {
      soundOffTimerRef.current = setTimeout(() => {
        soundOffTimerRef.current = null;
        applySoundActive(false);
      }, SOUND_HOLD_MS);
    }
  }, [volume, isRecording, isPaused]);
  useEffect(() => clearSoundOffTimer, []);

  // 장시간 회의(수천 줄)에서 모든 줄을 그리면 스크롤·입력이 무거워진다.
  // 화면엔 최근 MAX_VISIBLE_LINES 줄만 두고, 전체는 종료 후 전사 문서에서 본다.
  const MAX_VISIBLE_LINES = 250;
  const hiddenLineCount = Math.max(0, liveTranscript.length - MAX_VISIBLE_LINES);
  const visibleTranscript = hiddenLineCount
    ? liveTranscript.slice(-MAX_VISIBLE_LINES)
    : liveTranscript;
  // 서버가 환각 의심 구간에 붙이는 표시(text_filters.SUSPECT_MARKER)가 화면에
  // 보이면 그게 무엇인지 알려준다 — 표시가 있을 때만 범례를 띄운다.
  const hasSuspectMark = visibleTranscript.some((s) => s.text?.includes("[불명]"));

  // 세그먼트 렌더링 key용 안정 id 발급기 — 텍스트 스트리밍 중 행 리마운트(깜빡임) 방지
  const segSeqRef = useRef(0);
  const newSegId = () => `seg-${++segSeqRef.current}`;
  // 백엔드 WS 연결/준비 타임아웃 — accept 후 서버가 멈추면 '연결 중...'에 갇히는 것 방지
  const connectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    (window as any).isRecordingActive = isRecording;
    if (isRecording) {
      (window as any).stopActiveRecording = () => stopRecordingRef.current && stopRecordingRef.current();
    } else {
      delete (window as any).stopActiveRecording;
    }
  }, [isRecording]);

  const stopAll = useCallback(() => {
    if (timerRef.current) clearInterval(timerRef.current);
    if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    if (processorRef.current) {
      processorRef.current.onaudioprocess = null;
      processorRef.current.disconnect();
    }
    if (silentOscRef.current) {
      try { silentOscRef.current.stop(); } catch {}
      silentOscRef.current.disconnect();
    }
    if (audioContextRef.current) audioContextRef.current.close().catch(() => {});
    if (streamRef.current) streamRef.current.getTracks().forEach(t => t.stop());
    if (displayStreamRef.current) displayStreamRef.current.getTracks().forEach(t => t.stop());
    audioContextRef.current = null;
    processorRef.current = null;
    silentOscRef.current = null;
    streamRef.current = null;
    displayStreamRef.current = null;
    analyserRef.current = null;
    setVolume(0);
    setSystemAudioOn(false);
  }, []);

  useEffect(() => {
    return () => stopAll();
  }, [stopAll]);

  // 화면 잠금/앱 전환 후 복귀 시 AudioContext 자동 resume
  useEffect(() => {
    const handleVisibility = () => {
      if (document.visibilityState === "visible" && audioContextRef.current?.state === "suspended") {
        audioContextRef.current.resume().catch(() => {});
      }
    };
    document.addEventListener("visibilitychange", handleVisibility);
    return () => document.removeEventListener("visibilitychange", handleVisibility);
  }, []);

  // Seamless WS Rotation every 14 minutes (840 seconds) — 직접 OpenAI 연결 전용.
  // 백엔드 모드에서는 서버가 OpenAI 연결을 소유하므로 로테이션 불필요.
  const isRotatingRef = useRef(false);
  useEffect(() => {
    if (backendModeRef.current) return;
    if (duration > 0 && duration % 840 === 0 && isRecording && !isPaused) {
      setWsStatus("연결 갱신 중...");
      const oldWs = wsRef.current;
      try {
        isRotatingRef.current = true; // 회전 중 플래그 — onclose에서 재연결 방지
        const newWs = createRealtimeWS();
        const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];
        const instructions = preset.translate
            ? "You are a bilingual meeting assistant. The user will speak. Your ONLY job is to immediately translate what is said into Korean. Output ONLY the translation without any intro. Do NOT answer questions, just translate them."
            : "You are a dictation assistant. Transcribe what the user says accurately.";

        newWs.onopen = () => {
          newWs.send(JSON.stringify({
            type: "session.update",
            session: {
              instructions: topic ? `${instructions}\n\nContext/Topic: ${topic}` : instructions,
              voice: "alloy",
              // 받아쓰기 모드에선 모델 응답 생성 불필요 — 토큰·지연 낭비 방지
              turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 800, create_response: preset.translate },
              input_audio_transcription: { model: "whisper-1" }
            }
          }));
          wsRef.current = newWs;
          setWsStatus("연결됨 (OpenAI 실시간)");
          // 이전 WS 핸들러 제거 후 종료 (재연결 트리거 방지)
          if (oldWs && oldWs.readyState === WebSocket.OPEN) {
            oldWs.onclose = null;
            oldWs.onerror = null;
            oldWs.close();
          }
          isRotatingRef.current = false;
        };
        newWs.onmessage = oldWs ? oldWs.onmessage : null;
        newWs.onerror = oldWs ? oldWs.onerror : null;
        newWs.onclose = oldWs ? oldWs.onclose : null;
      } catch (e) {
        console.error("WS Rotation failed", e);
        isRotatingRef.current = false;
      }
    }
  }, [duration, isRecording, isPaused, modeNum, topic]);

  // ── 백엔드 모드: 서버 /ws/realtime 경유 (STT + 실시간 vault 검색 + 회의록 생성) ──
  const startBackendRecording = async () => {
    const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];
    const ws = createBackendRealtimeWS();
    wsRef.current = ws;
    provisionalIdxRef.current = {};
    serverSessionIdRef.current = null;
    captureStartedRef.current = false;

    // 15초 안에 ready/fallback_http가 안 오면 연결 실패로 보고 초기 화면으로 복귀
    if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
    connectTimerRef.current = setTimeout(() => {
      if (!captureStartedRef.current) {
        try { ws.onclose = null; ws.onerror = null; ws.onmessage = null; ws.close(); } catch {}
        wsRef.current = null;
        setWsStatus("서버 연결이 지연되고 있습니다 — 잠시 후 다시 시도해주세요.");
        setStatus("idle");
      }
    }, 15000);

    ws.onopen = () => {
      ws.send(JSON.stringify({
        config: {
          mode: modeNum,
          title, topic, speakers,
          language: preset.language,
          translate: preset.translate,
          type: preset.type,
          // 이번 녹음 한정 STT 모델 오버라이드(비었으면 서버가 설정 기본값 사용)
          ...(sttModel ? { stt_model: sttModel } : {}),
        },
      }));
      setWsStatus("서버 연결됨");
    };

    ws.onmessage = (evt) => {
      let msg: any;
      try { msg = JSON.parse(evt.data); } catch { return; }

      switch (msg.type) {
        case "session_created":
          serverSessionIdRef.current = msg.sessionId;
          setSessionId(msg.sessionId);
          break;

        case "ready":
          if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
          setWsStatus(`실시간 전사 중 (${msg.model})`);
          if (!captureStartedRef.current) { captureStartedRef.current = true; startAudioCapture(); }
          break;

        case "fallback_http":
          // WS 실시간이 안 되면 서버가 같은 오디오 스트림을 HTTP 청크로 전사(자동 폴백).
          // ready 후 캡처가 이미 시작됐다면 다시 시작하지 않는다(중복 캡처 방지).
          if (connectTimerRef.current) clearTimeout(connectTimerRef.current);
          setHttpFallback(true);
          setWsStatus(`전사 중 (HTTP 청크 방식: ${msg.model})`);
          if (!captureStartedRef.current) { captureStartedRef.current = true; startAudioCapture(); }
          break;

        case "fallback_provider":
          // OpenAI 두 모델이 모두 실패해 다른 벤더(Groq)로 전사가 넘어갔다.
          // 조용히 바꾸지 않고 한 번 알린다 — 화자분리·모델 품질이 달라지기 때문.
          setWsStatus(`전사 중 (${msg.provider} 백업: ${msg.model}) — OpenAI 응답 실패로 자동 전환`);
          break;

        case "delta": {
          const id = msg.itemId || "item";
          setLiveTranscript(prev => {
            const copy = [...prev];
            const idx = provisionalIdxRef.current[id];
            if (idx === undefined || !copy[idx]) {
              copy.push({ id: `ws-${id}`, text: msg.delta || "", translatedText: "", speaker: speakers || "", start: -1, end: 0 });
              provisionalIdxRef.current[id] = copy.length - 1;
            } else {
              copy[idx] = { ...copy[idx], text: copy[idx].text + (msg.delta || "") };
            }
            return copy;
          });
          break;
        }

        case "segment": {
          const segItemId: string | undefined = msg.itemId;
          setLiveTranscript(prev => {
            const copy = [...prev];
            const seg: RealtimeSegment = {
              text: msg.text || "",
              translatedText: msg.translatedText || "",
              speaker: msg.speaker || speakers || "",
              start: msg.start ?? 0,
              end: msg.end ?? 0,
              provisional: !!msg.provisional,
            };
            // itemId로 스트리밍(provisional) 항목을 확정본으로 교체 — itemId가 없으면(HTTP 폴백 청크 모드)
            // provisional 항목 자체가 없으므로 그대로 append. 교체 시 기존 id를 유지해 리마운트 방지.
            const idx = segItemId !== undefined ? provisionalIdxRef.current[segItemId] : undefined;
            if (idx !== undefined && copy[idx]) copy[idx] = { ...seg, id: copy[idx].id };
            else copy.push({ ...seg, id: newSegId() });
            return copy;
          });
          if (segItemId !== undefined) delete provisionalIdxRef.current[segItemId];
          break;
        }

        case "translation": {
          // 빠른 패스 세그먼트의 비동기 번역 도착 — start/end로 매칭해 채워 넣는다.
          setLiveTranscript(prev => prev.map(s =>
            Math.abs(s.start - (msg.start ?? -1)) < 0.01 && Math.abs(s.end - (msg.end ?? -1)) < 0.01
              ? { ...s, translatedText: msg.translatedText || "" } : s));
          break;
        }

        case "revise": {
          // 2-pass 보정: [fromTime, toTime) 구간의 조각 세그먼트를 보정된 문장으로 교체.
          // start<0(WS delta 임시 항목)은 보존 — HTTP 모드에선 존재하지 않는다.
          const from = msg.fromTime ?? 0;
          const to = msg.toTime ?? 0;
          setLiveTranscript(prev => {
            const kept = prev.filter(s => s.start < 0 || s.start < from || s.start >= to);
            const revised: RealtimeSegment[] = (msg.segments || []).map((s: any) => ({
              id: newSegId(),
              text: s.text || "",
              translatedText: s.translatedText || "",
              speaker: s.speaker || speakers || "",
              start: s.start ?? 0,
              end: s.end ?? 0,
              provisional: false,
            }));
            return [...kept, ...revised].sort((a, b) =>
              (a.start < 0 ? Infinity : a.start) - (b.start < 0 ? Infinity : b.start));
          });
          break;
        }

        case "related_notes":
          // 실시간 관련 노트 — 내부(vault) 검색 결과 + (게이트 시) 웹 보완.
          // status 만 실려 오는 이벤트는 배지 갱신용(notes 는 비어 있음).
          if (msg.status) setWikiStatus(msg.status as WikiSearchStatus);
          // 껐으면 늦게 도착한 결과도 넣지 않는다 — 서버가 멈추기 전에 이미 나간
          // 검색이 있을 수 있다(끄자마자 칩이 하나 더 뜨면 "안 꺼졌다"로 읽힌다).
          if (wikiMutedRef.current) break;
          if ((msg.notes || []).length > 0) {
            setRelatedNotes(prev => {
              const merged = [...(msg.notes || []).map((n: any) => ({
                filename: n.filename || "",
                title: n.title || (n.filename || "").split(/[\\/]/).pop()?.replace(/\.md$/, "") || "",
                score: n.score || 0,
                snippet: n.snippet || "",
                heading: n.heading || "",
                sectionPath: n.sectionPath || "",
                sourceType: n.sourceType || "note",
                foundBy: n.foundBy || "",
                segmentText: n.segmentText || "",
                rankScore: n.rankScore ?? 0,
              })), ...prev];
              // 중복 제거는 **제목** 기준 — 서버의 표시용 dedupe_by_title 과 같은 규칙.
              // filename 으로 걸렀던 과거엔 같은 제목의 다른 경로 노트(예:
              // 01_References/Companies/Acme.md 와 Archive/…/회사/Acme.md)가 서로 다른
              // 발화에서 잡히면 화면에 [[Acme]] 칩이 두 개 떴다.
              const seen = new Set<string>();
              const deduped = merged.filter(n => {
                const key = (n.title || n.filename || "").trim().toLowerCase();
                if (!key || seen.has(key)) return false;
                seen.add(key);
                return true;
              });
              return sortRelated(deduped).slice(0, 20);
            });
          }
          break;

        case "facilitation": {
          // 페르소나 개입 1건. 사용자가 이번 회의에 끄면 그 뒤로는 받지 않는다.
          if (facMutedRef.current) break;
          const item = msg as Facilitation;
          if (!item.id) break;
          // dedup 은 ref 로 한다 — setState 업데이터 안에서 판정하면 StrictMode 가
          // 업데이터를 두 번 돌릴 때 비용이 두 번 더해진다.
          if (facSeenIdsRef.current.has(item.id)) break;
          facSeenIdsRef.current.add(item.id);
          setInterventions(prev => [item, ...prev].slice(0, 30));  // 최신 우선, 상한 30
          if (item.costUsd) setFacCostUsd(c => c + item.costUsd!);
          if (item.kind === "brief") setFacBriefBusy(false);
          // 대기(소극) 항목이 방출되면 배지를 줄인다
          setFacPending(p => (item.level < 3 && p > 0 ? p - 1 : p));
          break;
        }

        case "facilitation_status": {
          const st = msg as FacilitationStatus;
          if (st.kind === "pending") setFacPending(st.pending || 0);
          else if (st.kind === "ready") setFacBriefOn(!!st.briefOn);
          else if (st.kind === "briefing") setFacBriefBusy(true);
          else {
            // 건너뜀 사유(한도·연타·내용 없음)가 오면 '정리 중…'을 풀어 준다 —
            // 안 풀면 버튼이 영구히 잠긴 것처럼 보인다.
            setFacBriefBusy(false);
            setFacStatus(st);
          }
          break;
        }

        case "status":
        case "generating":
          setWsStatus(msg.message || "");
          break;

        case "fact_check":
          setWsStatus("사실 검증 결과 수신 — 회의록에 반영됨");
          break;

        case "completed": {
          if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
          const sid = msg.sessionId || serverSessionIdRef.current;
          setStatus("completed");
          setWsStatus(msg.minutesSkipped
            ? (msg.message || "내용이 짧아 회의록 없이 전사만 저장했습니다.")
            : `문서 생성 완료 (세그먼트 ${msg.segmentCount ?? "?"}개)`);
          try { ws.close(); } catch {}
          if (sid) {
            // 미러 실패 시 몇 차례 재시도 후 이동 — 첫 시도 실패로 상세 화면이
            // '세션을 찾을 수 없습니다' 막다른 화면이 되는 것을 방지한다.
            (async () => {
              let ok = false;
              for (let i = 0; i < 4 && !ok; i++) {
                if (i > 0) await new Promise((r) => setTimeout(r, 2000));
                try { ok = await mirrorServerSession(sid); } catch { ok = false; }
              }
              setTimeout(() => onComplete(sid), 400);
            })();
          } else {
            // sessionId를 못 받은 예외 상황 — 상세로 갈 수 없으니 최소한 대시보드로 복귀
            // (녹음 화면에 멈춰 있지 않도록).
            setTimeout(() => onExit?.(), 800);
          }
          break;
        }

        case "empty": {
          // 서버가 '음성 미감지'로 세션을 저장하지 않고 종료 — completed가 안 오므로
          // 여기서 대기를 풀고 대시보드로 복귀시킨다(멈춤 방지).
          if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
          setStatus("completed");
          setWsStatus(msg.message || "음성이 감지되지 않았습니다.");
          try { ws.close(); } catch {}
          // reason="stt_failed" 는 조치가 필요한 두 경우를 함께 덮는다 — 인식 호출이
          // 실패했거나, 호출은 됐는데 빈 결과만 돌아온 경우(후자는 마이크 음량 문제일
          // 수도 있어 서버 문구가 둘 다 안내한다). 읽을 시간을 더 준다.
          // (무발화는 볼 것이 없으니 기존처럼 바로 복귀.)
          setTimeout(() => onExit?.(), msg.reason === "stt_failed" ? 6000 : 1500);
          break;
        }

        case "cancelled": {
          // 서버가 취소를 확인 — (프런트에서 이미 리셋했으면 핸들러가 제거돼 안 온다)
          if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
          try { ws.close(); } catch (e) {}
          stopAll();
          setIsRecording(false);
          setIsPaused(false);
          setLiveTranscript([]);
          setDuration(0);
          setSessionId(null);
          setWsStatus("");
          setStatus("idle");
          break;
        }

        case "error": {
          setWsStatus(`오류: ${msg.message || "알 수 없는 오류"}`);
          // 생성/종료 단계에서의 에러면 녹음 화면에 멈추지 않고 대시보드로 복귀.
          // (전사 중 개별 청크 오류 통지는 녹음을 계속하므로 이동하지 않는다.)
          // isRecording 대신 window 플래그 사용 — 이 핸들러 클로저의 isRecording은
          // 생성 시점(false) 값이라 stale하다.
          if (!(window as any).isRecordingActive) {
            if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
            try { ws.close(); } catch {}
            setTimeout(() => onExit?.(), 2000);
          }
          break;
        }
      }
    };

    ws.onerror = () => setWsStatus("서버 연결 오류");

    ws.onclose = () => {
      setStatus(prev => {
        if (prev === "recording") {
          // 서버가 수신분까지는 저장·처리하므로 데이터 유실은 없음
          setWsStatus("서버 연결이 끊겼습니다 — 수신된 오디오까지는 서버가 저장·처리합니다. 나중에 대시보드에서 확인하세요.");
          try { KeepAwake.allowSleep(); } catch {}
          stopAll();
          setIsRecording(false);
          return "error";
        }
        if (prev === "generating") {
          // 생성 중 소켓이 끊겨 completed를 못 받은 경우 — 서버는 수신분을 계속
          // 처리·저장하므로, 녹음 화면에 멈추지 말고 대시보드로 복귀시킨다(나중에
          // Sessions에서 확인 가능).
          setWsStatus("생성 중 연결이 끊겼습니다 — 서버에서 계속 처리됩니다. 대시보드에서 확인하세요.");
          if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
          setTimeout(() => onExit?.(), 2000);
          return "completed";
        }
        return prev;
      });
    };
  };

  const startRecording = async () => {
    try {
      setStatus("connecting");
      // 녹음이 시작되면 세션 설정 카드는 접는다 — 녹음 중엔 볼 일이 없고, 화면
      // 위쪽을 차지해 전사 영역이 좁아진다(사용자가 다시 펼 수 있음).
      setIsSettingsCollapsed(true);
      setLiveTranscript([]);
      // 끄기는 **그 회의에서만** 유효하다 — 새 녹음에서는 다시 켜진다(페르소나와 동일).
      resetRelatedNotes();
      resetFacilitation();
      setBackendMode(false);
      setDuration(0);
      setHttpFallback(false);
      setCaptureNote("");
      try {
        await KeepAwake.keepAwake();
        await Haptics.impact({ style: ImpactStyle.Heavy });
      } catch(e) {} // Request Wakelock & Haptic on mobile

      // 로컬 백엔드가 있으면 서버 경유 (실시간 wiki 검색 + 서버 회의록 생성),
      // 없으면 기존 직접 OpenAI 연결로 폴백 (모바일 단독 배포)
      backendModeRef.current = await backendAvailable();
      // 관련 노트 바는 서버 경유 녹음에서만 의미가 있다(직접 OpenAI 연결 경로는
      // vault 검색을 하지 않는다) — 단독 모드에서 빈 바가 계속 떠 있지 않게 한다.
      setBackendMode(backendModeRef.current);
      if (backendModeRef.current) {
        // 사전 점검: 키 없이 시작하면 서버 error 이벤트로만 실패가 보여
        // 비개발자는 원인을 알기 어렵다. 시작 전에 확인해 설정으로 안내한다.
        try {
          const cfg = await getConfig();
          if (!cfg?.api?.openai_api_key) {
            alert("OpenAI API 키가 설정되지 않았습니다.\n[설정] → API 키에서 입력한 뒤 녹음을 시작하세요.");
            setStatus("idle");
            return;
          }
        } catch {
          // 설정 조회 실패는 차단하지 않는다 — 서버가 최종 검증한다.
        }
        await startBackendRecording();
        return;
      }

      // WebSocket 직접 연결 (OpenAI Realtime API)
      const ws = createRealtimeWS();
      wsRef.current = ws;

      const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];

      ws.onopen = () => {
        const instructions = preset.translate 
            ? "You are a bilingual meeting assistant. The user will speak. Your ONLY job is to immediately translate what is said into Korean. Output ONLY the translation without any intro. Do NOT answer questions, just translate them."
            : "You are a dictation assistant. Transcribe what the user says accurately.";
            
        ws.send(JSON.stringify({
          type: "session.update",
          session: {
            instructions: topic ? `${instructions}\n\nContext/Topic: ${topic}` : instructions,
            voice: "alloy",
            turn_detection: {
              type: "server_vad",
              threshold: 0.5,
              prefix_padding_ms: 300,
              silence_duration_ms: 800,
              // 받아쓰기 모드에선 모델 응답 생성 불필요 — 토큰·지연 낭비 방지
              create_response: preset.translate
            },
            input_audio_transcription: {
              model: "whisper-1"
            }
          }
        }));
        setWsStatus("연결됨 (OpenAI 실시간)");
        startAudioCapture();
      };

      ws.onmessage = (evt) => {
        let msg: any;
        try { msg = JSON.parse(evt.data); } catch { return; }

        switch (msg.type) {
          case "session.created":
          case "session.updated":
            setSessionId(msg.session?.id || "realtime-session");
            break;

          case "input_audio_buffer.speech_started":
            // duration을 직접 읽으면 핸들러 생성 시점(0)에 고정되므로 stateRef 사용
            setLiveTranscript(prev => [
              ...prev,
              { id: newSegId(), text: "(듣는 중...)", translatedText: "", speaker: speakers || "Speaker", start: stateRef.current.duration, end: 0 }
            ]);
            break;

          case "conversation.item.input_audio_transcription.completed":
            setLiveTranscript(prev => {
              if (prev.length === 0) return prev;
              const copy = [...prev];
              copy[copy.length - 1].text = msg.transcript;
              return copy;
            });
            break;

          case "response.audio_transcript.delta":
            setLiveTranscript(prev => {
              if (prev.length === 0) return prev;
              const copy = [...prev];
              copy[copy.length - 1].translatedText = (copy[copy.length - 1].translatedText || "") + msg.delta;
              return copy;
            });
            break;

          case "response.audio_transcript.done":
            setLiveTranscript(prev => {
              if (prev.length === 0) return prev;
              const copy = [...prev];
              if (msg.transcript) {
                 copy[copy.length - 1].translatedText = msg.transcript;
              }
              return copy;
            });
            break;

          case "error":
            console.error("[OpenAI Error]", msg.error);
            setWsStatus(`오류: ${msg.error?.message || "알 수 없는 오류"}`);
            break;
        }
      };

      ws.onerror = () => {
        setWsStatus("연결 오류 — 재연결 중...");
      };

      ws.onclose = (event) => {
        // WS Rotation 중이면 재연결 안 함
        if (isRotatingRef.current) return;
        // 녹음 중 비정상 종료 시 자동 재연결 (최대 3회)
        setStatus(prev => {
          if (prev === "recording") {
            const retryCount = (wsRef.current as any)?._retryCount || 0;
            if (retryCount < 3) {
              setWsStatus(`재연결 중... (${retryCount + 1}/3)`);
              setTimeout(() => {
                try {
                  const newWs = createRealtimeWS();
                  (newWs as any)._retryCount = retryCount + 1;
                  wsRef.current = newWs;
                  newWs.onopen = ws.onopen;
                  newWs.onmessage = ws.onmessage;
                  newWs.onerror = ws.onerror;
                  newWs.onclose = ws.onclose;
                } catch {
                  setWsStatus("재연결에 실패했습니다");
                  setStatus("error");
                }
              }, 1000 * (retryCount + 1));
              return "recording"; // 재연결 중에도 녹음 상태 유지
            }
            setWsStatus("재연결 3회 실패 — 연결이 끊겼습니다");
            try { KeepAwake.allowSleep(); } catch {}
            return "error";
          }
          return prev;
        });
      };

    } catch (err) {
      console.error("Recording start error:", err);
      alert("OpenAI 연결을 시작할 수 없습니다. 설정에서 API 키를 확인하세요.");
      setStatus("idle");
    }
  };

  /** 이 PC에서 나는 소리(온라인 회의 상대방 목소리)를 캡처한다.
   *  Chrome은 시스템 오디오를 video 요청과 함께만 내주므로 video:true 로 받고 영상 트랙은
   *  즉시 버린다(영상은 쓰지도, 보내지도 않는다). 사용자가 공유 창에서 '시스템 오디오도
   *  공유'를 켜지 않으면 오디오 트랙이 없는데, 그때 조용히 넘어가면 상대 발언이 통째로 빈
   *  기록이 남는다 → 알린 뒤 마이크만으로 계속한다(녹음 자체를 실패시키지는 않는다). */
  const captureSystemAudio = async (): Promise<MediaStream | null> => {
    if (!HAS_DISPLAY_MEDIA) {
      setCaptureNote("이 브라우저는 PC 소리 캡처를 지원하지 않습니다 — 마이크만 녹음합니다.");
      return null;
    }
    try {
      const ds: MediaStream = await (navigator.mediaDevices as any).getDisplayMedia({
        video: true,
        audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false },
      });
      ds.getVideoTracks().forEach((t) => t.stop());
      const audioTracks = ds.getAudioTracks();
      if (audioTracks.length === 0) {
        ds.getTracks().forEach((t) => t.stop());
        setCaptureNote("이 PC 소리를 받지 못했습니다 — 공유 창에서 [시스템 오디오도 공유]를 "
          + "켜고 다시 시작하세요. 지금은 마이크만 녹음합니다.");
        return null;
      }
      // 사용자가 Chrome의 [공유 중지]를 누르면 트랙이 끝난다 — 이후로는 마이크만 들어오므로
      // 그 사실을 화면에 남긴다.
      audioTracks[0].onended = () => {
        setSystemAudioOn(false);
        setCaptureNote("이 PC 소리 공유가 중지됐습니다 — 이후로는 마이크만 녹음됩니다.");
      };
      return ds;
    } catch {
      setCaptureNote("이 PC 소리 공유가 취소됐습니다 — 마이크만 녹음합니다.");
      return null;
    }
  };

  const startAudioCapture = async () => {
    // 재연결 등으로 onopen이 다시 실행돼도 캡처 파이프라인을 중복 생성하지 않는다.
    // (과거엔 재연결마다 stream/AudioContext/타이머가 하나씩 늘어 타이머가 2배속으로
    // 돌고 메모리가 새는 버그가 있었다 — onaudioprocess는 wsRef를 참조하므로 기존
    // 파이프라인이 새 소켓으로 계속 전송한다.)
    if (streamRef.current || processorRef.current) {
      setIsRecording(true);
      setStatus("recording");
      return;
    }
    try {
      // 에코 취소·자동 게인은 상황별 기준값(NEAR_FIELD/FAR_FIELD)에서 온다. 나머지는 공통.
      const modeDef = CAPTURE_MODES.find((m) => m.value === captureMode) || CAPTURE_MODES[0];
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: { ideal: 24000 },
          channelCount: 1,
          noiseSuppression: { ideal: false }, // 원본 음질 유지 (노이즈 억제로 음질 손상 방지)
          ...modeDef.audio,
        },
      });
      streamRef.current = stream;

      const audioContext = new AudioContext({ sampleRate: 24000 });
      audioContextRef.current = audioContext;

      // 무음 오실레이터: iOS가 백그라운드에서도 오디오 세션을 유지하도록 함
      const silentOsc = audioContext.createOscillator();
      const silentGain = audioContext.createGain();
      silentGain.gain.value = 0.001; // 사실상 무음
      silentOsc.connect(silentGain);
      silentGain.connect(audioContext.destination);
      silentOsc.start();
      silentOscRef.current = silentOsc;

      const source = audioContext.createMediaStreamSource(stream);

      // 믹싱 지점 — 마이크와(선택 시) 이 PC 소리를 여기 한 노드로 합류시킨다. 아래
      // analyser·processor 는 예전과 똑같이 이 노드 하나만 보므로 다운스트림(PCM16 전송·
      // 백엔드·2-pass 보정)은 전혀 바뀌지 않는다.
      const mixed = audioContext.createGain();
      source.connect(mixed);

      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      mixed.connect(analyser);
      analyserRef.current = analyser;

      const bufferSize = 4096;
      const processor = audioContext.createScriptProcessor(bufferSize, 1, 1);
      mixed.connect(processor);
      processor.connect(audioContext.destination);
      processorRef.current = processor;

      const arrayBufferToBase64 = (buffer: ArrayBuffer) => {
        let binary = '';
        const bytes = new Uint8Array(buffer);
        const len = bytes.byteLength;
        for (let i = 0; i < len; i++) {
          binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
      };

      processor.onaudioprocess = (e) => {
        if (isPausedRef.current) return;
        const input = e.inputBuffer.getChannelData(0);
        const int16 = new Int16Array(input.length);
        for (let i = 0; i < input.length; i++) {
          const s = Math.max(-1, Math.min(1, input[i]));
          int16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
        }
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          if (backendModeRef.current) {
            // 백엔드 모드: 바이너리 PCM16 프레임 (base64/JSON 오버헤드 없음)
            wsRef.current.send(int16.buffer);
          } else {
            wsRef.current.send(JSON.stringify({
               type: "input_audio_buffer.append",
               audio: arrayBufferToBase64(int16.buffer)
            }));
          }
        }
      };

      // 볼륨 지표: 시간영역 RMS. 과거엔 주파수 빈 128개 전체 평균이라 음성 에너지가
      // 고주파(≈0) 빈에 희석돼, 실제로 말하는 중에도 '무음'으로 표시되기 일쑤였다.
      const dataArray = new Uint8Array(analyser.fftSize);
      const updateVolume = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteTimeDomainData(dataArray);
        let sumSq = 0;
        for (let i = 0; i < dataArray.length; i++) {
          const d = dataArray[i] - 128;
          sumSq += d * d;
        }
        setVolume(Math.sqrt(sumSq / dataArray.length) * 3); // 기존 0~100 스케일 근사
        animFrameRef.current = requestAnimationFrame(updateVolume);
      };
      updateVolume();

      timerRef.current = setInterval(() => setDuration(prev => prev + 1), 1000);

      setIsRecording(true);
      setIsPaused(false);
      setStatus("recording");

      // 이 PC 소리는 마이크가 이미 흐르기 시작한 **뒤에** 붙인다. 공유 창을 고르는 데는
      // 몇 초~수십 초가 걸리는데, 그동안 마이크 파이프라인까지 멈춰 있으면 그 구간 발화가
      // 통째로 유실된다(회의 도중에 녹음을 시작하면 바로 겪는다). mixed 노드는 이미 살아
      // 있으므로 사용자가 공유를 허용하는 순간 라이브로 합류한다.
      // 여기서 실패해도 이미 돌아가는 마이크 녹음을 죽이면 안 된다(아래 catch 는 마이크
      // 권한 거부용이라 안내가 엉뚱해지고 세션이 idle 로 돌아간다) → 자체 try 로 가둔다.
      if (modeDef.system) {
        try {
          const sysStream = await captureSystemAudio();
          if (sysStream) {
            // 공유를 고르는 사이 사용자가 녹음을 멈췄을 수 있다. 그때 stopAll()이 이미
            // AudioContext 를 닫았으므로 여기서 노드를 만들면 예외가 난다 — 스트림만 정리한다.
            if (audioContextRef.current === audioContext) {
              audioContext.createMediaStreamSource(sysStream).connect(mixed);
              displayStreamRef.current = sysStream;
              setSystemAudioOn(true);
            } else {
              sysStream.getTracks().forEach((t) => t.stop());
            }
          }
        } catch (e) {
          console.error("System audio attach failed:", e);
          setCaptureNote("이 PC 소리를 붙이지 못했습니다 — 마이크만 녹음합니다.");
        }
      }

    } catch (err: any) {
      console.error("Audio capture error:", err);
      if (err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError") {
        alert(
          "마이크 권한이 거부되었습니다.\n\n" +
          "PC(브라우저): 주소창 왼쪽 자물쇠 아이콘 → 사이트 권한에서 마이크를 '허용'으로 바꾸고 새로고침하세요.\n" +
          "(Windows 설정 > 개인 정보 > 마이크에서 접근 허용도 켜져 있어야 합니다)\n\n" +
          "iPhone: 설정 > 개인정보 보호 > 마이크에서 이 앱을 허용해주세요."
        );
      } else {
        alert("마이크에 접근할 수 없습니다. 다른 프로그램이 마이크를 사용 중인지, 권한이 허용돼 있는지 확인해주세요.");
      }
      setStatus("idle");
      setIsRecording(false);
    }
  };

  const pauseRecording = () => {
    if (!isRecording) return;
    if (isPaused) {
      isPausedRef.current = false;
      timerRef.current = setInterval(() => setDuration(prev => prev + 1), 1000);
      setIsPaused(false);
    } else {
      isPausedRef.current = true;
      if (timerRef.current) clearInterval(timerRef.current);
      setIsPaused(true);
    }
  };

  const stopRecording = () => {
    if (backendModeRef.current) {
      // 백엔드 모드: stop을 보내고 소켓을 유지 — 서버가 회의록 생성 후
      // completed 이벤트를 보낸다 (수 분 소요 가능, 10분 타임아웃)
      try {
        KeepAwake.allowSleep();
        Haptics.notification({ type: NotificationType.Success });
      } catch(e) {}
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ type: "stop" }));
      }
      stopAll();
      setIsRecording(false);
      setIsPaused(false);
      setStatus("generating");
      setWsStatus("서버에서 회의록을 생성하는 중...");
      completionTimerRef.current = setTimeout(() => {
        setStatus("completed");
        setWsStatus("서버에서 계속 처리 중입니다 — 나중에 대시보드에서 확인하세요.");
        try { wsRef.current?.close(); } catch {}
      }, 600000);
      return;
    }

    const finalTranscript = transcriptRef.current;
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.close();
    }
    try {
      KeepAwake.allowSleep();
      Haptics.notification({ type: NotificationType.Success });
    } catch(e) {}
    stopAll();
    setIsRecording(false);
    setIsPaused(false);
    setStatus("generating");

    const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];
    import("../lib/api").then(api => {
       api.saveCompleteSession({ title, topic, type: preset.type, duration, translate: preset.translate }, finalTranscript).then(id => {
          setStatus("completed");
          api.generateSummaryForSession(id).catch(() => {});
          setTimeout(() => onComplete(id), 1000);
       }).catch(err => {
          console.error("Session save failed:", err);
          setStatus("error");
          setWsStatus("세션 저장에 실패했습니다. 다시 시도하세요.");
       });
    }).catch(err => {
       console.error("Module load failed:", err);
       setStatus("error");
       setWsStatus("세션 저장에 실패했습니다.");
    });
  };
  // 렌더 중 ref 대입은 부수효과다 — 효과로 옮긴다(동작 동일, StrictMode 안전).
  useEffect(() => { stopRecordingRef.current = stopRecording; });

  // 저장하지 않고 취소 — 서버에 cancel을 보내 세션을 삭제시키고 즉시 새 녹음 가능 상태로.
  const cancelRecording = () => {
    if (!confirm("이 녹음을 저장하지 않고 버릴까요?\n(회의록을 만들지 않습니다)")) return;
    try { KeepAwake.allowSleep(); } catch (e) {}
    const ws = wsRef.current;
    if (ws) {
      // 리셋 후 onclose가 '연결 끊김' 오류 화면으로 덮지 않도록 핸들러 제거
      ws.onclose = null; ws.onerror = null; ws.onmessage = null;
      try {
        if (ws.readyState === WebSocket.OPEN) {
          if (backendModeRef.current) ws.send(JSON.stringify({ type: "cancel" }));
          setTimeout(() => { try { ws.close(); } catch (e) {} }, 300);
        } else {
          ws.close();
        }
      } catch (e) {}
    }
    wsRef.current = null;
    if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
    stopAll();
    setIsRecording(false);
    setIsPaused(false);
    setLiveTranscript([]);
    resetRelatedNotes();
    resetFacilitation();
    setDuration(0);
    setSessionId(null);
    setWsStatus("");
    setStatus("idle");
  };

  // 생성 중 이탈 — 회의록 생성은 서버에서 계속되고(완료 후 대시보드에 표시),
  // 화면은 즉시 새 녹음을 시작할 수 있는 상태로 돌아간다.
  const startNewWhileGenerating = () => {
    if (!confirm("회의록 생성은 서버에서 계속 진행됩니다(완료 후 대시보드에서 확인).\n지금 새 녹음을 준비할까요?")) return;
    if (completionTimerRef.current) clearTimeout(completionTimerRef.current);
    const ws = wsRef.current;
    if (ws) {
      ws.onclose = null; ws.onerror = null; ws.onmessage = null;
      try { ws.close(); } catch (e) {}
    }
    wsRef.current = null;
    setLiveTranscript([]);
    resetRelatedNotes();
    resetFacilitation();
    setDuration(0);
    setSessionId(null);
    setWsStatus("");
    setStatus("idle");
  };

  // 시계 표기는 lib/format.formatClock 하나를 쓴다 — 여기 있던 지역 formatDuration 은
  // lib 의 같은 이름 함수와 결과가 달랐고(HH:MM:SS vs "3m 20s"), formatTimestamp 는
  // 호출부가 0건인 죽은 코드였다.

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];

  const soundDetected = isRecording && !isPaused && soundActive;

  // 인스펙터 탭 개수 — 접었을 때도 숫자가 남아야 미확인 항목이 조용히 사라지지 않는다.
  const inspectorTabs = [
    { key: "notes" as const, label: "관련 노트", count: relatedNotes.length },
    { key: "personas" as const, label: "진행 도우미", count: interventions.length },
  ];
  const pinnedCount = interventions.filter((i) => i.persona === "fact_checker").length;

  // 평문 연결 상태 한 줄(FR-REC-3) — WS/HTTP·제공자 폴백 같은 구현 용어는 여기 오지 않는다.
  // wsStatus 는 내부 진행 문구라 그대로 노출하지 않고, 사용자가 알아야 할 것만 옮겨 적는다.
  const connectionNote = !isRecording ? ""
    : httpFallback
      ? "표시가 몇 초 늦을 수 있어요 · 말한 내용은 자동으로 저장됩니다"
      : "말한 내용은 자동으로 저장됩니다";

  const captureLabel = CAPTURE_MODES.find((m) => m.value === captureMode)?.label || "내 마이크만";

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <RecordingHeader
        status={status}
        isPaused={isPaused}
        duration={duration}
        volume={volume}
        soundDetected={soundDetected}
        rates={costRates}
        translate={preset.translate}
        facilitationUsd={facCostUsd}
        facilitationCount={facSeenIdsRef.current.size}
        modeLabel={preset.label}
        captureLabel={captureLabel}
        connectionNote={connectionNote}
      />

      {/* 이 PC 소리 캡처가 기대와 다르게 끝났을 때 — 조용히 마이크만 녹음되면 나중에야
          상대 발언이 통째로 빈 걸 알게 된다. */}
      {captureNote && (
        <div className="mb-2">
          <Banner title="이 PC 소리" onDismiss={() => setCaptureNote("")}>{captureNote}</Banner>
        </div>
      )}

      {/* 인-세션 설정 패널(FR-REC-2) — 녹음 중에는 읽기 전용이다. 시작과 함께 접히지만
          "지금 어떤 설정으로 녹음 중인지" 확인할 자리는 남아 있어야 한다. */}
      <div className="mb-2">
        <button type="button" onClick={() => setIsSettingsCollapsed((v) => !v)}
          aria-expanded={!isSettingsCollapsed}
          className="inline-flex items-center gap-1 rounded-ctl px-1.5 py-0.5 text-sm font-semibold text-ink-2 hover:bg-hover">
          <ChevronDown size={13} aria-hidden="true"
            className={`transition-transform ${isSettingsCollapsed ? "-rotate-90" : ""}`} />
          {isSettingsCollapsed ? "설정 보기" : "설정 숨기기"}
        </button>

        {!isSettingsCollapsed && (
          <div className="mt-1.5 grid gap-3 rounded-card border border-line bg-surface p-3 shadow-card lg:grid-cols-2">
            <div className="space-y-2.5">
              <TextField label="세션 제목" id="rec-title" value={title} disabled={isRecording}
                placeholder="예: 주간 제품 회의" onChange={(e) => setTitle(e.target.value)} />
              <TextField label="참석자" id="rec-speakers" value={speakers} disabled={isRecording}
                placeholder="예: 홍길동, 김영희, 이철수" onChange={(e) => setSpeakers(e.target.value)} />
              <Field label="주제 / 맥락" htmlFor="rec-topic"
                description="회의 배경을 적으면 용어 인식과 회의록 품질이 올라갑니다.">
                <Textarea id="rec-topic" value={topic} disabled={isRecording} rows={2}
                  onChange={(e) => setTopic(e.target.value)} />
              </Field>

              <Field label="음성 인식 (이번 녹음만)" htmlFor="rec-stt"
                description="설정 기본값이 자동 선택됩니다. 여기서 바꿔도 설정은 그대로입니다.">
                <Select id="rec-stt" value={sttModel} disabled={isRecording}
                  onChange={(e) => setSttModel(e.target.value)}>
                  {/* 저장된 기본값이 목록에 없더라도 항상 선택돼 보이도록 보강 */}
                  {sttModel && !STT_OPTIONS.some((o) => o.value === sttModel) && (
                    <option value={sttModel}>설정 기본값</option>
                  )}
                  {STT_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </Select>
              </Field>

              <fieldset disabled={isRecording} className="space-y-1">
                <legend className="text-base font-medium text-ink">소리 잡는 법</legend>
                {CAPTURE_MODES.filter((m) => !m.system || HAS_DISPLAY_MEDIA).map((m) => (
                  <label key={m.value}
                    className={`flex cursor-pointer items-start gap-2 rounded-ctl border px-2.5 py-2 transition-colors ${
                      captureMode === m.value
                        ? "border-accent bg-accent-weak"
                        : "border-line bg-surface-2 hover:border-line-strong"
                    } ${isRecording ? "cursor-not-allowed opacity-60" : ""}`}>
                    <input type="radio" name="captureMode" value={m.value} className="mt-1 shrink-0"
                      checked={captureMode === m.value} disabled={isRecording}
                      onChange={() => {
                        setCaptureMode(m.value);
                        try { localStorage.setItem("CAPTURE_MODE", m.value); } catch { /* ignore */ }
                      }} />
                    <span className="min-w-0">
                      <span className="block text-base font-semibold text-ink">{m.label}</span>
                      <span className="block text-xs text-ink-3">{m.desc}</span>
                    </span>
                  </label>
                ))}
                <p className="text-xs leading-relaxed text-ink-3">
                  {captureMode === "mic+system"
                    ? "녹음이 시작된 직후 공유 창이 뜹니다 — [전체 화면]을 고르고 아래 [시스템 오디오도 공유]를 꼭 켜세요. 줌·팀즈 앱은 이렇게 해야 상대 목소리가 들어옵니다([창]을 고르면 크롬이 소리를 함께 주지 않습니다). 고르는 동안에도 마이크는 이미 녹음 중이고, 화면은 녹화되지 않습니다."
                    : captureMode === "room"
                    ? "멀리서 나는 소리가 지워지지 않도록 에코 취소를 끄고 마이크 감도를 올립니다."
                    : "헤드셋·근접 발화 기준입니다. 온라인 회의 상대 목소리나 회의실 TV 소리가 안 잡히면 위에서 상황을 바꿔보세요."}
                </p>
              </fieldset>
            </div>

            <ModePanel modeNum={modeNum} onChange={setModeNum} disabled={isRecording}
              hint="말한 내용이 실시간으로 전사됩니다. 표시까지 1~5초 정도 걸릴 수 있어요." />
          </div>
        )}
      </div>

      {isRecording || status === "generating" || status === "completed" ? (
        <div className="flex min-h-0 flex-1 gap-3">
          <div className="flex min-w-0 flex-1 flex-col">
            <TranscriptPanel
              segments={visibleTranscript}
              hiddenCount={hiddenLineCount}
              totalCount={liveTranscript.length}
              translate={preset.translate}
              viewMode={viewMode}
              onViewMode={setViewMode}
              flashStart={flashStart}
              hasSuspectMark={hasSuspectMark}
              status={status}
              isPaused={isPaused}
              soundDetected={soundDetected}
              systemAudioOn={systemAudioOn}
              roomMic={captureMode === "room"}
              followLatest={followLatest}
              unseenCount={unseenCount}
              onJumpLatest={jumpToLatest}
              onScroll={onTranscriptScroll}
              panelRef={transcriptPanelRef}
            />

            {/* 컨트롤 — [정지]는 회의를 끝내고 문서 생성(비용)을 시작하는 버튼이라
                아이콘만 두지 않는다. */}
            <div className="mt-2 flex flex-wrap items-center gap-2">
              {status === "recording" && (
                <>
                  <Button variant="secondary" icon={isPaused ? Play : Pause} onClick={pauseRecording}>
                    {isPaused ? "재시작" : "일시정지"}
                  </Button>
                  <Button variant="danger" icon={Square} onClick={stopRecording}>
                    정지 &amp; 회의록 생성
                  </Button>
                  <Button variant="ghost" onClick={cancelRecording} className="ml-auto text-rec">
                    저장하지 않고 취소
                  </Button>
                </>
              )}
              {status === "generating" && (
                <>
                  <StatusPill tone="proc" pulse>회의록을 만드는 중…</StatusPill>
                  <Button variant="secondary" size="sm" onClick={startNewWhileGenerating}>
                    새 녹음 시작 (생성은 백그라운드에서 계속)
                  </Button>
                </>
              )}
            </div>
          </div>

          {/* 우측 인스펙터(FR-REC-6) — 관련 노트 / 진행 도우미. 모바일에서는 같은 내용이
              하단 트리거 바 + 바텀시트로 나온다(Inspector 가 함께 책임진다). */}
          <Inspector
            tabs={inspectorTabs}
            value={inspectorTab}
            onChange={setInspectorTab}
            label="관련 노트와 진행 도우미"
            mobileAlert={pinnedCount > 0
              ? <span className="text-rec">확인 필요 {pinnedCount}</span>
              : undefined}
          >
            {inspectorTab === "notes" ? (
              <RelatedNotesTab
                notes={relatedNotes}
                muted={wikiMuted}
                searchOff={!!wikiStatus && !wikiStatus.enabled}
                searchOffReason={wikiStatus?.reasonText}
                canMute={isRecording && backendMode}
                onMute={wikiMute}
                showEvidence={wikiExpanded}
                onToggleEvidence={() => setWikiExpanded((v) => !v)}
              />
            ) : (
              <PersonaPanel
                items={interventions}
                status={facStatus}
                pending={facPending}
                muted={facMuted}
                briefOn={facBriefOn && isRecording}
                briefBusy={facBriefBusy}
                onCheckNow={facCheckNow}
                onBriefNow={facBriefNow}
                onMute={facMute}
                onJump={jumpToSpan}
                onAck={(id) => facFeedback(id, "ack")}
                onDismiss={(id) => facFeedback(id, "dismiss")}
                emptyHint={backendMode
                  ? "아직 개입이 없습니다. 회의 진행 도우미가 켜져 있으면 필요할 때만 조용히 카드가 올라옵니다."
                  : "이 녹음은 PC 서버를 거치지 않아 진행 도우미가 동작하지 않습니다."}
              />
            )}
          </Inspector>
        </div>
      ) : (
        /* 유휴 — 시작 버튼과 녹음 고지. 녹음 중 상시 배너는 곧 안 읽히므로 여기에만 둔다. */
        <div className="flex flex-1 flex-col items-center justify-center gap-3 rounded-card
          border border-dashed border-line-strong bg-surface-2 py-12">
          <Button variant="danger" icon={Mic} onClick={startRecording}
            busy={status === "connecting"}>
            {status === "connecting" ? "연결 중…" : "녹음 시작"}
          </Button>
          <p className="text-sm text-ink-3">말하면 실시간으로 전사돼요 — 표시까지 몇 초 걸릴 수 있어요.</p>
          <p className="text-xs text-ink-3">
            녹음 전 참석자에게 <b>녹음·자동 전사</b> 사실을 알려 주세요.
          </p>
        </div>
      )}
    </div>
  );
}

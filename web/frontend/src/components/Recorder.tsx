import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Mic, Square, Play, Pause, Loader2, Volume2,
  Activity, Settings2, User, ChevronDown, Info, BookOpen,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import {
  createRealtimeWS, backendAvailable, createBackendRealtimeWS, mirrorServerSession,
  getCostRates, getConfig, type CostRates,
} from "../lib/api";
import { MODE_PRESETS, type RealtimeSegment } from "../lib/types";
import ModeSelector from "./ModeSelector";
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
const STT_OPTIONS: { value: string; label: string }[] = [
  { value: "gpt-4o-mini-transcribe", label: "gpt-4o-mini-transcribe — 저렴·빠름" },
  { value: "gpt-4o-transcribe", label: "gpt-4o-transcribe — 고정확" },
  { value: "gpt-4o-transcribe-diarize", label: "gpt-4o-transcribe-diarize — 화자분리(실시간은 자동 전환)" },
  { value: "whisper-1", label: "whisper-1 — 구형·안정" },
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
    desc: "Teams 등 온라인 회의 — 헤드셋을 써도 상대방 목소리가 함께 녹음됩니다",
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
  // WebSocket 실시간 전사가 안 돼 HTTP 청크 방식으로 자동 전환됐는지 여부.
  // (에러가 아니라 정상 폴백이므로 사용자에게 안내로만 노출한다.)
  const [httpFallback, setHttpFallback] = useState(false);
  // 백엔드 모드 — 서버가 STT/실시간 vault 검색/회의록 생성 수행 (API 키 미노출)
  const [relatedNotes, setRelatedNotes] = useState<RelatedNote[]>([]);
  const [wikiStatus, setWikiStatus] = useState<WikiSearchStatus | null>(null);
  // 관련 노트 근거(점수·섹션경로·snippet·발화) 펼침 — 사용자가 눌렀을 때만 펼친다
  // (자동 갱신으로는 절대 레이아웃이 움직이지 않게 하기 위함, FR-10)
  const [wikiExpanded, setWikiExpanded] = useState(false);
  // 서버 경유 녹음인지 (관련 노트 바 표시 조건 — 단독 OpenAI 경로는 vault 검색 없음)
  const [backendMode, setBackendMode] = useState(false);
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
      setRelatedNotes([]);
      setWikiStatus(null);
      setWikiExpanded(false);
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
  stopRecordingRef.current = stopRecording;

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
    setRelatedNotes([]);
    setWikiStatus(null);
    setWikiExpanded(false);
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
    setRelatedNotes([]);
    setWikiStatus(null);
    setWikiExpanded(false);
    setDuration(0);
    setSessionId(null);
    setWsStatus("");
    setStatus("idle");
  };

  const formatDuration = (s: number) => {
    const hrs = Math.floor(s / 3600);
    const mins = Math.floor((s % 3600) / 60);
    const secs = s % 60;
    return `${hrs > 0 ? hrs.toString().padStart(2, "0") + ":" : ""}${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  const formatTimestamp = (s: number) => {
    if (s < 0) return "Live";
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    return `${mins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
  };

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[2];

  const soundDetected = isRecording && !isPaused && soundActive;

  return (
      <div className="w-full max-w-4xl mx-auto bg-white border md:border-zinc-200 md:rounded-3xl md:shadow-xl overflow-hidden min-h-[calc(100dvh-5rem)] md:min-h-0 flex flex-col">
        {/* Status Header (컴팩트) */}
        <div className="bg-zinc-900 text-white px-4 py-3 md:px-5 md:py-4 shrink-0">
          <div className="flex flex-row items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 md:gap-3 min-w-0">
              <div className="relative shrink-0">
                <div className={`w-9 h-9 md:w-10 md:h-10 rounded-lg md:rounded-xl flex items-center justify-center transition-all duration-500 ${
                  isRecording ? (isPaused ? "bg-amber-500" : "bg-red-500 animate-pulse") : "bg-zinc-800"
                }`}>
                  <Mic className="w-5 h-5 text-white" />
                </div>
              </div>
              <div className="min-w-0">
                <h3 className="text-base md:text-lg font-bold tracking-tight leading-tight">
                  {status === "generating" ? "문서 생성 중..." :
                   status === "completed" ? "세션 완료" :
                   status === "connecting" ? "연결 중..." :
                   isRecording ? (isPaused ? "녹음 일시정지" : "녹음 중") : "녹음 준비 완료"}
                </h3>
                <div className="flex items-center gap-1.5 text-zinc-400 text-xs mt-0.5 truncate">
                  <Activity className="w-3.5 h-3.5 shrink-0" />
                  <span className="truncate">{wsStatus || (isRecording ? "스트리밍 중..." : "마이크 준비됨")}</span>
                </div>
              </div>
            </div>

            <div className="flex flex-col items-end shrink-0">
              <div className="text-xl md:text-2xl font-mono font-black tracking-tighter text-white tabular-nums leading-none">
                {formatDuration(duration)}
              </div>
              <div className="flex items-center gap-2 mt-1.5">
                <div className="w-16 md:w-24 h-1 bg-zinc-800 rounded-full overflow-hidden">
                  <motion.div
                    animate={{ width: `${Math.min(volume * 2, 100)}%` }}
                    className={`h-full transition-colors ${
                      volume > 40 ? "bg-red-500" : soundDetected ? "bg-emerald-500" : "bg-zinc-600"
                    }`}
                  />
                </div>
              </div>
              {costRates && (isRecording || status === "generating" || status === "completed") && (
                <span
                  className="text-[11px] text-zinc-400 mt-1 font-mono tabular-nums"
                  title={
                    // 2단계 보정 전사가 켜져 있으면 STT 과금이 두 번 난다. 과거에는
                    // 1차 단가만 곱해 실제의 약 1/3을 보여줬다.
                    (costRates.two_pass
                      ? `1차 인식 $${costRates.stt_per_min}/분 + 문장 보정 $${costRates.revise_per_min}/분`
                      : `음성 인식 $${costRates.stt_per_min}/분`)
                    + (preset.translate ? ` + 번역 $${costRates.translate_per_min}/분` : "")
                    + " + (완료 시) 회의록 생성비. 대략치입니다."
                  }
                >
                  💵 ~${(
                    (duration / 60) * (
                      // 구버전 백엔드(필드 없음)에서는 1차 단가로 폴백한다.
                      (costRates.stt_effective_per_min ?? costRates.stt_per_min)
                      + (preset.translate ? costRates.translate_per_min : 0)
                    )
                    + ((status === "generating" || status === "completed") ? costRates.minutes_flat : 0)
                  ).toFixed(3)}
                  {costRates.two_pass && <span className="text-zinc-500"> (2패스)</span>}
                </span>
              )}
            </div>
          </div>
        </div>

        {/* HTTP 청크 모드 안내 — 얇은 한 줄(설정상 기본값이거나 WS 폴백 모두 포함). 과거엔
            헤더에 큰 배너로 떠서 제일 먼저 화면을 채웠다. */}
        {httpFallback && isRecording && (
          <div className="bg-amber-50 border-b border-amber-100 text-amber-700 text-[11px] px-4 md:px-5 py-1.5 shrink-0">
            안정·저비용 <b>HTTP 청크 방식</b>으로 전사 중 — 말한 내용이 몇 초 뒤에 표시될 수 있어요.
          </div>
        )}

        {/* 이 PC 소리 캡처가 기대와 다르게 끝났을 때 — 같은 얇은 한 줄 규격 */}
        {captureNote && (
          <div className="bg-amber-50 border-b border-amber-100 text-amber-700 text-[11px] px-4 md:px-5 py-1.5 shrink-0 flex items-start gap-2">
            <span className="flex-1">{captureNote}</span>
            <button type="button" onClick={() => setCaptureNote("")}
              className="shrink-0 font-bold text-amber-600 hover:text-amber-800">닫기</button>
          </div>
        )}

        {/* 관련 노트 바 (wiki.realtime_vault_search) — 비방해 표시 규칙(FR-10):
            · 녹음 중에는 높이가 고정된 얇은 바를 항상 띄워 둔다 → 검색 결과가 새로
              들어와도 전사 본문이 밀리지 않는다(자동 리플로우 0).
            · 팝업·토스트·소리·포커스 이동 없음. 근거 펼치기는 사용자가 눌렀을 때만.
            · 내부(📄 노트 / 🎓 논문)를 웹(🌐)보다 앞줄에 둔다(sortRelated).
            · 백엔드 미연결/비활성 사유도 같은 바에 조용히 배지로 표시(FR-1). */}
        {((isRecording && backendMode) || relatedNotes.length > 0
          || (wikiStatus && !wikiStatus.enabled)) && (
          <div className="bg-emerald-50/70 border-b border-emerald-100 shrink-0">
            <div className="px-4 md:px-8 h-9 flex items-center gap-2 overflow-x-auto overflow-y-hidden">
              <span className="flex items-center gap-1.5 text-[10px] font-black uppercase tracking-widest text-emerald-600 shrink-0">
                <BookOpen className="w-3.5 h-3.5" /> 관련 노트
              </span>

              {/* 상태 배지 — 꺼짐/미연결 사유 (전사를 방해하지 않는 회색 톤) */}
              {wikiStatus && !wikiStatus.enabled && (
                <span
                  title={wikiStatus.reasonText}
                  className="shrink-0 text-[11px] bg-white border border-zinc-200 text-zinc-500 px-2 py-0.5 rounded-full whitespace-nowrap max-w-[60vw] truncate"
                >
                  검색 꺼짐 — {wikiStatus.reasonText}
                </span>
              )}

              {relatedNotes.slice(0, 8).map((n) => (
                <button
                  key={n.filename || n.title}
                  type="button"
                  onClick={() => setWikiExpanded(true)}
                  title={`${n.sectionPath || n.title}\n${n.snippet || n.filename}`}
                  className="shrink-0 text-xs font-medium bg-white border border-emerald-200 text-emerald-800 px-2.5 py-1 rounded-full whitespace-nowrap hover:border-emerald-400 transition-colors"
                >
                  {SOURCE_ICON[n.sourceType || "note"] || "📄"} [[{n.title}
                  {n.heading ? `#${n.heading}` : ""}]]
                </button>
              ))}
              {relatedNotes.length > 8 && (
                <span className="shrink-0 text-[10px] text-emerald-500 font-bold">
                  +{relatedNotes.length - 8}
                </span>
              )}
              {relatedNotes.length === 0 && (!wikiStatus || wikiStatus.enabled) && (
                <span className="shrink-0 text-[11px] text-emerald-600/60">
                  {wikiStatus?.enabled ? "발화와 관련된 내부 노트를 찾는 중…" : "대기 중…"}
                </span>
              )}
              {relatedNotes.length > 0 && (
                <button
                  type="button"
                  onClick={() => setWikiExpanded((v) => !v)}
                  className="ml-auto shrink-0 flex items-center gap-1 text-[10px] font-bold text-emerald-600 hover:text-emerald-800"
                >
                  <ChevronDown className={`w-3.5 h-3.5 transition-transform ${wikiExpanded ? "" : "-rotate-90"}`} />
                  {wikiExpanded ? "근거 접기" : "근거 보기"}
                </button>
              )}
            </div>

            {/* 근거 상세 — 사용자가 펼쳤을 때만. 자체 스크롤이라 전사 영역을 잠식하지 않는다. */}
            {wikiExpanded && relatedNotes.length > 0 && (
              <div className="px-4 md:px-8 pb-2 max-h-44 overflow-y-auto border-t border-emerald-100">
                {relatedNotes.map((n) => (
                  <div key={`ev-${n.filename || n.title}`} className="py-1.5 border-b border-emerald-100/60 last:border-0">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <span className="text-xs font-bold text-emerald-900">
                        {SOURCE_ICON[n.sourceType || "note"] || "📄"} {n.sectionPath || n.title}
                      </span>
                      <span className="text-[10px] font-mono text-zinc-400 tabular-nums">
                        score {(n.score ?? 0).toFixed(2)}
                        {n.foundBy ? ` · ${n.foundBy === "section" ? "섹션 일치" : n.foundBy === "web" ? "웹" : "노트 일치"}` : ""}
                      </span>
                      {n.filename && (
                        <span className="text-[10px] text-zinc-400 truncate max-w-[40vw]">{n.filename}</span>
                      )}
                    </div>
                    {n.snippet && (
                      <p className="text-[11px] text-zinc-600 mt-0.5 line-clamp-2">{n.snippet}</p>
                    )}
                    {n.segmentText && (
                      <p className="text-[10px] text-zinc-400 mt-0.5 italic truncate">발화: {n.segmentText}</p>
                    )}
                  </div>
                ))}
                <p className="text-[10px] text-zinc-400 pt-1.5">
                  녹음 중에는 노트로 이동하지 않습니다(녹음 보호). 종료 후 회의 상세의
                  <b> 참조된 관련 노트</b>에서 클릭해 열 수 있습니다.
                </p>
              </div>
            )}
          </div>
        )}

        <div className="flex-1 flex flex-col p-4 md:p-10">
          {/* Settings Toggle */}
          <div className="flex items-center justify-between mb-4 md:mb-6 shrink-0">
            <h4 className="text-sm font-bold text-zinc-400 uppercase tracking-widest">세션 설정</h4>
            <button
              onClick={() => setIsSettingsCollapsed(!isSettingsCollapsed)}
              className="flex items-center gap-2 text-xs font-bold text-zinc-500 hover:text-zinc-900 transition-colors"
            >
              <ChevronDown className={`w-4 h-4 transition-transform ${isSettingsCollapsed ? "-rotate-90" : ""}`} />
              {isSettingsCollapsed ? "설정 보기" : "설정 숨기기"}
            </button>
          </div>

          <AnimatePresence>
            {!isSettingsCollapsed && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: "auto", opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                className="overflow-hidden"
              >
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 mb-12">
                  <div className="space-y-6">
                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <Info className="w-3 h-3" /> 세션 제목
                      </label>
                      <input
                        type="text"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        placeholder="예: 주간 제품 회의"
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 font-medium"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <User className="w-3 h-3" /> 참석자
                      </label>
                      <input
                        type="text"
                        value={speakers}
                        onChange={(e) => setSpeakers(e.target.value)}
                        placeholder="예: 홍길동, 김영희, 이철수"
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 font-medium"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <Settings2 className="w-3 h-3" /> 주제 / 맥락
                      </label>
                      <textarea
                        value={topic}
                        onChange={(e) => setTopic(e.target.value)}
                        placeholder="정확도를 높이려면 회의 배경을 적어주세요..."
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 h-32 resize-none font-medium"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <Settings2 className="w-3 h-3" /> 음성 인식 모델 (이번 녹음만)
                      </label>
                      <select
                        value={sttModel}
                        onChange={(e) => setSttModel(e.target.value)}
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 font-medium text-sm"
                      >
                        {/* 저장된 기본값이 목록에 없더라도 항상 선택돼 보이도록 보강 */}
                        {sttModel && !STT_OPTIONS.some((o) => o.value === sttModel) && (
                          <option value={sttModel}>{sttModel} (설정 기본값)</option>
                        )}
                        {STT_OPTIONS.map((o) => (
                          <option key={o.value} value={o.value}>{o.label}</option>
                        ))}
                      </select>
                      <p className="text-[11px] text-zinc-400 leading-relaxed">
                        설정의 기본 모델이 자동 선택됩니다. 이번 녹음에만 다른 모델을 쓰려면 바꾸세요 — <b>설정 기본값은 그대로 유지</b>됩니다.
                      </p>
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <Mic className="w-3 h-3" /> 소리 잡는 방법
                      </label>
                      <div className="space-y-2">
                        {CAPTURE_MODES
                          .filter((m) => !m.system || HAS_DISPLAY_MEDIA)
                          .map((m) => (
                            <label
                              key={m.value}
                              className={`flex items-start gap-3 px-4 py-3 rounded-xl border cursor-pointer transition-all ${
                                captureMode === m.value
                                  ? "bg-zinc-900 border-zinc-900 text-white"
                                  : "bg-zinc-50 border-zinc-200 hover:border-zinc-300"
                              } ${isRecording ? "opacity-50 cursor-not-allowed" : ""}`}
                            >
                              <input
                                type="radio"
                                name="captureMode"
                                value={m.value}
                                checked={captureMode === m.value}
                                disabled={isRecording}
                                onChange={() => {
                                  setCaptureMode(m.value);
                                  try { localStorage.setItem("CAPTURE_MODE", m.value); } catch { /* ignore */ }
                                }}
                                className="mt-1 accent-zinc-900 shrink-0"
                              />
                              <span className="min-w-0">
                                <span className="block text-sm font-bold leading-tight">{m.label}</span>
                                <span className={`block text-[11px] mt-0.5 leading-relaxed ${
                                  captureMode === m.value ? "text-zinc-300" : "text-zinc-500"
                                }`}>{m.desc}</span>
                              </span>
                            </label>
                          ))}
                      </div>
                      <p className="text-[11px] text-zinc-400 leading-relaxed">
                        {captureMode === "mic+system"
                          ? "녹음이 시작된 직후 공유 창이 뜹니다 — [전체 화면]을 고르고 아래 [시스템 오디오도 공유]를 꼭 켜세요. 고르는 동안에도 마이크는 이미 녹음 중이고, 화면은 녹화되지 않습니다."
                          : captureMode === "room"
                          ? "멀리서 나는 소리가 지워지지 않도록 에코 취소를 끄고 마이크 감도를 올립니다."
                          : "헤드셋·근접 발화 기준입니다. 온라인 회의 상대 목소리나 회의실 TV 소리가 안 잡히면 위에서 상황을 바꿔보세요."}
                      </p>
                    </div>
                  </div>

                  <ModeSelector
                    modeNum={modeNum}
                    onChange={setModeNum}
                    disabled={isRecording}
                    hint="말한 내용이 실시간으로 전사됩니다. 연결 방식(WS/HTTP)에 따라 화면 표시까지 1~5초 정도 걸릴 수 있어요."
                  />
                </div>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Recording Area */}
          <div className={`flex-1 flex flex-col items-center justify-center ${isSettingsCollapsed && isRecording ? "py-2 md:py-6" : "py-8 md:py-12"} ${isRecording ? "" : "border-2 border-dashed border-brand-200 rounded-[2.5rem] bg-white/50"} backdrop-blur-sm transition-all duration-500 min-h-[300px]`}>
            <AnimatePresence mode="wait">
              {isRecording || status === "generating" || status === "completed" ? (
                <motion.div
                  key="recording"
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.95 }}
                  className="w-full h-full flex flex-col items-center gap-6 md:gap-10 px-0 md:px-10"
                >
                  {/* Live Transcript — 높이를 화면 비율로 제한하고 패널 안에서만
                      스크롤한다. 과거엔 상위에 확정 높이가 없어 flex-1 이 내용만큼
                      늘어나 페이지 전체가 길어지고(한국어는 특히 줄이 빨리 쌓인다)
                      정지 버튼이 화면 밖으로 밀려났다. */}
                  <div
                    ref={transcriptPanelRef}
                    onScroll={onTranscriptScroll}
                    className={`w-full max-w-4xl glass-panel md:rounded-[2rem] p-3 md:p-5 min-h-[220px] max-h-[42dvh] md:max-h-[52dvh] overflow-y-auto overscroll-contain flex flex-col gap-2 md:gap-3 transition-all duration-700 relative`}
                  >
                    {/* 배경이 없으면 전사 텍스트가 이 줄 뒤로 비쳐 지나간다(좌측 라벨은
                        칩과 달리 자체 배경이 없었다). 패널 좌우 패딩만큼 넓혀 덮는다. */}
                    <div className="sticky top-0 z-10 flex items-center justify-between gap-2 -mx-3 md:-mx-5 px-3 md:px-5 pb-2 bg-white/85 backdrop-blur-md">
                      {/* 전사 줄 수 — 얼마나 쌓였는지, 화면에 안 보이는 앞부분이 있는지 알려준다 */}
                      <span className="text-[10px] font-bold text-brand-300 tabular-nums pl-1">
                        {liveTranscript.length > 0 && `전사 ${liveTranscript.length}줄`}
                        {hiddenLineCount > 0 && ` (앞 ${hiddenLineCount}줄은 종료 후 전사 문서에서)`}
                      </span>
                       <div className="inline-flex items-center gap-2 bg-white/80 backdrop-blur-md px-3 py-1.5 rounded-full shadow-sm">
                        {/* 녹음 중에는 실제 마이크 입력(소리 감지/무음)을 여기 표시 — 전사를
                            보는 바로 그 자리에서 소리가 들어가는지 즉시 알 수 있게 한다. */}
                        {status === "recording" ? (
                          <>
                            <div className={`w-2 h-2 rounded-full ${
                              isPaused ? "bg-amber-500" : soundDetected ? "bg-emerald-500 animate-pulse" : "bg-zinc-300"
                            }`} />
                            <span className={`text-[10px] font-bold uppercase tracking-widest ${
                              isPaused ? "text-amber-500" : soundDetected ? "text-emerald-600" : "text-zinc-400"
                            }`}>
                              {isPaused ? "일시정지" : soundDetected ? "🎤 소리 감지 중" : "무음 — 소리 없음"}
                            </span>
                            {/* 기본이 아닌 캡처 방식은 녹음 중에도 여기 남긴다 — 세션 설정은
                                시작과 함께 접히므로, 상대 목소리가 실제로 들어오는 방식으로
                                녹음 중인지 확인할 자리가 사라진다. 'PC 소리'는 고른 값이
                                아니라 성사된 상태(systemAudioOn)를 표시한다. */}
                            {systemAudioOn && (
                              <span title="이 PC에서 나는 소리(온라인 회의 상대방 목소리)가 함께 녹음되고 있습니다."
                                className="text-[10px] font-bold text-sky-600 border-l border-zinc-200 pl-2">
                                🖥 PC 소리 포함
                              </span>
                            )}
                            {captureMode === "room" && (
                              <span title="에코 취소를 끄고 마이크 감도를 올린 상태입니다(멀리서 나는 소리용)."
                                className="text-[10px] font-bold text-sky-600 border-l border-zinc-200 pl-2">
                                🏢 회의실 마이크
                              </span>
                            )}
                          </>
                        ) : (
                          <>
                            <div className={`w-2 h-2 rounded-full ${status === "generating" ? "bg-amber-500 animate-pulse" : status === "completed" ? "bg-emerald-500" : "bg-red-500 animate-pulse"}`} />
                            <span className="text-[10px] font-bold text-brand-400 uppercase tracking-widest">
                              {status === "generating" ? "처리 중" : status === "completed" ? "완료" : "실시간 스트리밍"}
                            </span>
                          </>
                        )}
                      </div>
                    </div>

                    {liveTranscript.length === 0 ? (
                      <div className="flex flex-col items-center justify-center flex-1 text-brand-300 gap-4 min-h-[200px]">
                        <div className="relative">
                          <Loader2 className="animate-spin" size={32} />
                          <Activity className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-brand-400" size={14} />
                        </div>
                        <p className="text-sm font-medium tracking-wide">
                          {status === "generating"
                            ? "회의 문서를 생성하는 중..."
                            : isPaused
                              ? "일시정지됨"
                              : soundDetected
                                ? "🎤 소리 감지 중 — 전사를 기다리는 중..."
                                : "오디오를 듣는 중... (아직 소리가 감지되지 않았어요)"}
                        </p>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-2 md:gap-2.5">
                        {visibleTranscript.map((item) => (
                          <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            // 안정 id 기반 key — 과거 시간+내용 키는 스트리밍으로 텍스트가
                            // 변할 때마다 행이 리마운트돼 깜빡였다.
                            key={item.id ?? `${item.start.toFixed(2)}-${item.text.slice(0, 16)}`}
                            className="flex flex-col w-full"
                          >
                            {item.speaker && (
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-brand-400">
                                  {item.speaker}
                                </span>
                                <div className="h-px flex-1 bg-brand-100" />
                              </div>
                            )}
                            
                            {(item.translatedText || (preset.translate && item.start >= 0)) ? (
                              // 번역 모드: 좌=영어(보조·작게), 우=한국어(주·조금 크게).
                              // 번역이 아직 안 왔어도 2열 틀을 유지해 도착 시 레이아웃이
                              // 출렁이지 않게 하고, 자리에 '번역 중…'을 표시한다.
                              // min-w-0: grid item 의 기본 min-width:auto 는 자식이 줄어들지
                              // 못하게 해서 줄바꿈 속성만으로는 칼럼이 넓어지는 것을 못 막는다.
                              <div className={`flex flex-col md:grid md:grid-cols-2 gap-0.5 md:gap-3 w-full items-start ${item.provisional ? "opacity-60" : ""}`}>
                                {/* Source (English) — 보조 */}
                                <p className={`ko-text min-w-0 text-xs md:text-[13px] leading-snug ${item.start === -1 ? "text-zinc-400 italic" : "text-zinc-500"}`}>
                                  {item.text}{item.start === -1 && " …"}
                                </p>
                                {/* Translated (Korean) — 주 */}
                                <p className={`ko-text min-w-0 text-sm md:text-base leading-snug font-medium md:border-l-2 md:border-l-brand-300 md:pl-3 ${item.translatedText ? "text-brand-900" : "text-brand-300 italic"}`}>
                                  {item.translatedText || "번역 중…"}
                                </p>
                              </div>
                            ) : (
                              // 받아쓰기(비번역) 모드: 더 작게, 한 줄로 조밀하게.
                              // provisional(빠른 패스 임시 조각)은 흐리게 — 보정되면 선명해진다.
                              <p className={`ko-text text-[13px] md:text-sm leading-snug ${
                                item.start === -1 ? "text-brand-400 italic"
                                : item.provisional ? "text-zinc-400"
                                : "text-brand-900"}`}>
                                {item.text}{item.start === -1 && " …"}
                              </p>
                            )}
                          </motion.div>
                        ))}
                        <div className="h-4" />
                      </div>
                    )}

                    {hasSuspectMark && (
                      <p className="text-[10px] text-brand-400 border-t border-brand-100 pt-2 mt-1">
                        <b>[불명]</b> 표시는 음성인식이 잘못 만들어낸 구간입니다(주로 무음·잡음
                        구간). 회의록에는 반영되지 않습니다.
                      </p>
                    )}

                    {/* 위를 읽는 동안 자동 스크롤이 멈추므로, 돌아갈 방법을 항상 준다 */}
                    {!followLatest && liveTranscript.length > 0 && (
                      <button
                        onClick={jumpToLatest}
                        className="sticky bottom-0 self-center mt-1 inline-flex items-center gap-1.5 bg-brand-900 text-white text-xs font-bold px-3.5 py-2 rounded-full shadow-lg hover:bg-brand-800 transition-colors"
                      >
                        <ChevronDown className="w-3.5 h-3.5" />
                        최신 전사로{unseenCount > 0 ? ` (새 ${unseenCount}줄)` : ""}
                      </button>
                    )}
                  </div>

                  {/* Controls */}
                  {status === "recording" && (
                    <div className="flex flex-col items-center gap-4 md:gap-6 shrink-0 pb-4">
                      {/* Audio Level Wave — 실제 입력 볼륨 기반. 무음이면 잔잔(bar가
                          거의 안 움직이고 흐려짐), 소리가 들어오면 볼륨에 비례해 커진다.
                          과거엔 Math.random()이라 무음에도 춤춰 '소리 들어가는 척' 착시를 줬다. */}
                      <div className="flex gap-1.5 md:gap-2 items-end h-10 md:h-16">
                        {[...Array(24)].map((_, i) => {
                          const level = Math.min(volume / 40, 1);            // 0~1 정규화
                          const shape = 0.35 + 0.65 * Math.sin(((i + 1) / 25) * Math.PI); // 가운데 높은 형태
                          const h = isPaused ? 4 : 4 + level * 44 * shape;
                          return (
                            <div
                              key={i}
                              style={{ height: `${h}px` }}
                              className={`w-1 md:w-1.5 rounded-full transition-[height,background-color] duration-150 ${
                                isPaused ? "bg-brand-300" : soundDetected ? "bg-brand-900" : "bg-brand-200"
                              }`}
                            />
                          );
                        })}
                      </div>

                      <div className="flex items-center gap-6 md:gap-8">
                        {/* 라벨 없는 글리프뿐이었다 — 특히 [정지]는 회의를 끝내고 문서
                            생성(비용)을 시작하는 버튼이라 무엇인지 분명해야 한다.
                            아이콘 아래 텍스트 + title/aria-label 을 모두 붙인다. */}
                        <button
                          onClick={pauseRecording}
                          title={isPaused ? "녹음 다시 시작" : "녹음 일시정지"}
                          aria-label={isPaused ? "녹음 다시 시작" : "녹음 일시정지"}
                          className={`w-14 h-14 md:w-16 md:h-16 rounded-2xl flex flex-col items-center justify-center gap-0.5 transition-all shadow-xl ${
                            isPaused ? "bg-emerald-500 hover:bg-emerald-600" : "bg-amber-500 hover:bg-amber-600"
                          } text-white hover:scale-105 active:scale-95`}
                        >
                          {isPaused ? <Play size={20} fill="currentColor" /> : <Pause size={20} fill="currentColor" />}
                          <span className="text-[9px] font-bold tracking-tight">
                            {isPaused ? "재시작" : "일시정지"}
                          </span>
                        </button>
                        <button
                          onClick={stopRecording}
                          title="녹음을 끝내고 회의록을 생성합니다"
                          aria-label="녹음 정지 및 회의록 생성"
                          className="w-20 h-20 md:w-24 md:h-24 bg-brand-950 text-white rounded-[1.5rem] md:rounded-[2rem] flex flex-col items-center justify-center gap-1 hover:bg-brand-900 transition-all shadow-2xl hover:scale-105 active:scale-95 group relative overflow-hidden"
                        >
                          <Square size={24} fill="currentColor" className="group-hover:scale-90 transition-transform relative z-10" />
                          <span className="text-[10px] font-bold tracking-tight relative z-10">정지</span>
                        </button>
                      </div>

                      <p className="text-[10px] md:text-xs text-brand-400 font-bold uppercase tracking-[0.3em] animate-pulse">
                        {isPaused ? "녹음 일시정지" : "세션 진행 중"}
                      </p>

                      <button
                        onClick={cancelRecording}
                        className="text-xs text-brand-400 hover:text-red-500 underline underline-offset-2 transition-colors"
                      >
                        저장하지 않고 취소
                      </button>
                    </div>
                  )}

                  {status === "generating" && (
                    <div className="flex flex-col items-center gap-3">
                      <div className="flex items-center gap-3 text-amber-600">
                        <Loader2 className="animate-spin" size={20} />
                        <span className="text-sm font-bold">AI가 회의 문서를 생성하는 중...</span>
                      </div>
                      <button
                        onClick={startNewWhileGenerating}
                        className="px-4 py-2 bg-white border border-brand-200 text-brand-600 rounded-xl text-xs font-semibold hover:bg-brand-50 transition-all"
                      >
                        새 녹음 시작 (생성은 백그라운드에서 계속)
                      </button>
                    </div>
                  )}
                </motion.div>
              ) : (
                <motion.div
                  key="idle"
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0, scale: 0.9 }}
                  className="flex flex-col items-center gap-8"
                >
                  <button
                    onClick={startRecording}
                    disabled={status === "connecting"}
                    className="w-32 h-32 bg-zinc-900 text-white rounded-full flex items-center justify-center hover:bg-zinc-800 transition-all shadow-2xl hover:scale-105 active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed group"
                  >
                    {status === "connecting" ? (
                      <Loader2 size={48} className="animate-spin" />
                    ) : (
                      <Mic size={48} className="group-hover:scale-110 transition-transform" />
                    )}
                  </button>
                  <div className="text-center">
                    <p className="text-lg font-bold text-zinc-900">새 세션 시작</p>
                    <p className="text-sm text-zinc-500 mt-1">
                      말하면 실시간으로 전사돼요 — 표시까지 몇 초 걸릴 수 있어요
                    </p>
                    {/* 녹음 전 화면에만 둔다 — 녹음 중 상시 배너는 곧 안 읽힌다. */}
                    <p className="text-xs text-zinc-400 mt-3">
                      녹음 전 참석자에게 <b>녹음·자동 전사</b> 사실을 알려 주세요.
                    </p>
                  </div>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </div>
      </div>
  );
}

import React, { useState, useEffect, useRef, useCallback } from "react";
import {
  Mic, Square, Play, Pause, Loader2, Volume2,
  Activity, Settings2, User, ChevronDown, Info,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { createRealtimeWS } from "../lib/api";
import { MODE_PRESETS, type RealtimeSegment } from "../lib/types";
import ModeSelector from "./ModeSelector";
import { KeepAwake } from '@capacitor-community/keep-awake';
import { Haptics, ImpactStyle, NotificationType } from '@capacitor/haptics';

export default function Recorder({ onComplete }: { onComplete: (id: string) => void }) {
  const [isRecording, setIsRecording] = useState(false);
  const [isPaused, setIsPaused] = useState(false);
  const [duration, setDuration] = useState(0);
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [modeNum, setModeNum] = useState(2);
  const [speakers, setSpeakers] = useState("");
  const [isSettingsCollapsed, setIsSettingsCollapsed] = useState(false);
  const [liveTranscript, setLiveTranscript] = useState<RealtimeSegment[]>([]);
  const [status, setStatus] = useState<string>("idle");
  const [volume, setVolume] = useState(0);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [wsStatus, setWsStatus] = useState<string>("");

  const transcriptEndRef = useRef<HTMLDivElement>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const silentOscRef = useRef<OscillatorNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
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
      // Auto-save on accidental unmount
      if ((window as any).isRecordingActive) {
        const finalTranscript = transcriptRef.current;
        const current = stateRef.current;
        const preset = MODE_PRESETS[current.modeNum] || MODE_PRESETS[2];
        import("../lib/api").then(api => {
           api.saveCompleteSession({ title: current.title, topic: current.topic, type: preset.type, duration: current.duration, translate: preset.translate }, finalTranscript).catch(()=>{});
        }).catch(()=>{});
      }
      (window as any).isRecordingActive = false;
      delete (window as any).stopActiveRecording;
      stopAll();
    };
  }, []);

  const transcriptRef = useRef<RealtimeSegment[]>([]);
  useEffect(() => {
    transcriptRef.current = liveTranscript;
    if (transcriptEndRef.current) {
      transcriptEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [liveTranscript]);

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
    audioContextRef.current = null;
    processorRef.current = null;
    silentOscRef.current = null;
    streamRef.current = null;
    analyserRef.current = null;
    setVolume(0);
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

  // Seamless WS Rotation every 14 minutes (840 seconds)
  const isRotatingRef = useRef(false);
  useEffect(() => {
    if (duration > 0 && duration % 840 === 0 && isRecording && !isPaused) {
      setWsStatus("Rotating connection...");
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
              turn_detection: { type: "server_vad", threshold: 0.5, prefix_padding_ms: 300, silence_duration_ms: 800 },
              input_audio_transcription: { model: "whisper-1" }
            }
          }));
          wsRef.current = newWs;
          setWsStatus("Connected (GPT-4o Realtime)");
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

  const startRecording = async () => {
    try {
      setStatus("connecting");
      setLiveTranscript([]);
      setDuration(0);
      try { 
        await KeepAwake.keepAwake(); 
        await Haptics.impact({ style: ImpactStyle.Heavy }); 
      } catch(e) {} // Request Wakelock & Haptic on mobile

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
              silence_duration_ms: 800
            },
            input_audio_transcription: {
              model: "whisper-1"
            }
          }
        }));
        setWsStatus("Connected (GPT-4o Realtime)");
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
            setLiveTranscript(prev => [
              ...prev,
              { text: "(Listening...)", translatedText: "", speaker: speakers || "Speaker", start: duration, end: 0 }
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
            setWsStatus(`Error: ${msg.error?.message || "Unknown error"}`);
            break;
        }
      };

      ws.onerror = () => {
        setWsStatus("Connection error - reconnecting...");
      };

      ws.onclose = (event) => {
        // WS Rotation 중이면 재연결 안 함
        if (isRotatingRef.current) return;
        // 녹음 중 비정상 종료 시 자동 재연결 (최대 3회)
        setStatus(prev => {
          if (prev === "recording") {
            const retryCount = (wsRef.current as any)?._retryCount || 0;
            if (retryCount < 3) {
              setWsStatus(`Reconnecting... (${retryCount + 1}/3)`);
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
                  setWsStatus("Reconnection failed");
                  setStatus("error");
                }
              }, 1000 * (retryCount + 1));
              return "recording"; // 재연결 중에도 녹음 상태 유지
            }
            setWsStatus("Connection lost after 3 retries");
            try { KeepAwake.allowSleep(); } catch {}
            return "error";
          }
          return prev;
        });
      };

    } catch (err) {
      console.error("Recording start error:", err);
      alert("Could not start connecting to OpenAI. Check API Key in Settings.");
      setStatus("idle");
    }
  };

  const startAudioCapture = async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: { ideal: 24000 },
          channelCount: 1,
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: false }, // 원본 음질 유지 (노이즈 억제로 음질 손상 방지)
          autoGainControl: { ideal: false },  // 자동 게인 제어 비활성 (원본 레벨 유지)
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

      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 256;
      source.connect(analyser);
      analyserRef.current = analyser;

      const bufferSize = 4096;
      const processor = audioContext.createScriptProcessor(bufferSize, 1, 1);
      source.connect(processor);
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
          wsRef.current.send(JSON.stringify({
             type: "input_audio_buffer.append",
             audio: arrayBufferToBase64(int16.buffer)
          }));
        }
      };

      const dataArray = new Uint8Array(analyser.frequencyBinCount);
      const updateVolume = () => {
        if (!analyserRef.current) return;
        analyserRef.current.getByteFrequencyData(dataArray);
        let sum = 0;
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
        setVolume(sum / dataArray.length);
        animFrameRef.current = requestAnimationFrame(updateVolume);
      };
      updateVolume();

      timerRef.current = setInterval(() => setDuration(prev => prev + 1), 1000);

      setIsRecording(true);
      setIsPaused(false);
      setStatus("recording");

    } catch (err: any) {
      console.error("Audio capture error:", err);
      if (err?.name === "NotAllowedError" || err?.name === "PermissionDeniedError") {
        alert("마이크 권한이 거부되었습니다.\n\niPhone: 설정 > 개인정보 보호 > 마이크에서 이 앱을 허용해주세요.");
      } else {
        alert("마이크에 접근할 수 없습니다. 권한을 확인해주세요.");
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
          setWsStatus("Failed to save session. Please try again.");
       });
    }).catch(err => {
       console.error("Module load failed:", err);
       setStatus("error");
       setWsStatus("Failed to save session.");
    });
  };
  stopRecordingRef.current = stopRecording;

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

  return (
      <div className="bg-white border md:border-zinc-200 md:rounded-3xl md:shadow-xl overflow-hidden min-h-[calc(100dvh-5rem)] md:min-h-0 flex flex-col">
        {/* Status Header */}
        <div className="bg-zinc-900 text-white p-6 md:p-8 shrink-0">
          <div className="flex flex-row items-center justify-between gap-4">
            <div className="flex items-center gap-3 md:gap-4">
              <div className="relative">
                <div className={`w-12 h-12 md:w-16 md:h-16 rounded-xl md:rounded-2xl flex items-center justify-center transition-all duration-500 ${
                  isRecording ? (isPaused ? "bg-amber-500" : "bg-red-500 animate-pulse") : "bg-zinc-800"
                }`}>
                  <Mic className="w-8 h-8 text-white" />
                </div>
                {isRecording && !isPaused && (
                  <motion.div
                    initial={{ scale: 0.8, opacity: 0 }}
                    animate={{ scale: 1.5, opacity: 0 }}
                    transition={{ repeat: Infinity, duration: 1.5 }}
                    className="absolute inset-0 bg-red-500 rounded-2xl -z-10"
                  />
                )}
              </div>
              <div>
                <h3 className="text-2xl font-bold tracking-tight">
                  {status === "generating" ? "Generating Documents..." :
                   status === "completed" ? "Session Complete" :
                   status === "connecting" ? "Connecting..." :
                   isRecording ? (isPaused ? "Recording Paused" : "Recording Live") : "Ready to Record"}
                </h3>
                <div className="flex items-center gap-2 text-zinc-400 text-sm mt-1">
                  <Activity className="w-4 h-4" />
                  <span>{wsStatus || (isRecording ? "Streaming to OpenAI..." : "Microphone ready")}</span>
                </div>
              </div>
            </div>

            <div className="flex flex-col items-end">
              <div className="text-3xl md:text-5xl font-mono font-black tracking-tighter text-white tabular-nums">
                {formatDuration(duration)}
              </div>
              <div className="flex items-center gap-2 mt-1 md:mt-2">
                <div className="w-16 md:w-32 h-1.5 bg-zinc-800 rounded-full overflow-hidden">
                  <motion.div
                    animate={{ width: `${Math.min(volume * 2, 100)}%` }}
                    className={`h-full transition-colors ${volume > 40 ? "bg-red-500" : "bg-emerald-500"}`}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="flex-1 flex flex-col p-4 md:p-10">
          {/* Settings Toggle */}
          <div className="flex items-center justify-between mb-4 md:mb-6 shrink-0">
            <h4 className="text-sm font-bold text-zinc-400 uppercase tracking-widest">Session Configuration</h4>
            <button
              onClick={() => setIsSettingsCollapsed(!isSettingsCollapsed)}
              className="flex items-center gap-2 text-xs font-bold text-zinc-500 hover:text-zinc-900 transition-colors"
            >
              <ChevronDown className={`w-4 h-4 transition-transform ${isSettingsCollapsed ? "-rotate-90" : ""}`} />
              {isSettingsCollapsed ? "Show Settings" : "Hide Settings"}
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
                        <Info className="w-3 h-3" /> Session Title
                      </label>
                      <input
                        type="text"
                        value={title}
                        onChange={(e) => setTitle(e.target.value)}
                        placeholder="e.g. Weekly Product Sync"
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 font-medium"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <User className="w-3 h-3" /> Participants
                      </label>
                      <input
                        type="text"
                        value={speakers}
                        onChange={(e) => setSpeakers(e.target.value)}
                        placeholder="e.g. John, Sarah, Mike"
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 font-medium"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest flex items-center gap-2">
                        <Settings2 className="w-3 h-3" /> Topic / Context
                      </label>
                      <textarea
                        value={topic}
                        onChange={(e) => setTopic(e.target.value)}
                        placeholder="Provide context for better AI accuracy..."
                        disabled={isRecording}
                        className="w-full px-5 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 focus:border-transparent outline-none transition-all disabled:opacity-50 h-32 resize-none font-medium"
                      />
                    </div>
                  </div>

                  <ModeSelector
                    modeNum={modeNum}
                    onChange={setModeNum}
                    disabled={isRecording}
                    hint="Audio is streamed directly to OpenAI Realtime API for sub-second latency transcription."
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
                  {/* Live Transcript */}
                  <div className={`w-full max-w-4xl glass-panel md:rounded-[2rem] p-4 md:p-8 flex-1 overflow-y-auto flex flex-col gap-4 md:gap-6 transition-all duration-700 relative`}>
                    <div className="sticky top-0 z-10 flex justify-end pb-2">
                       <div className="inline-flex items-center gap-2 bg-white/80 backdrop-blur-md px-3 py-1.5 rounded-full shadow-sm">
                        <div className={`w-2 h-2 rounded-full ${status === "generating" ? "bg-amber-500 animate-pulse" : status === "completed" ? "bg-emerald-500" : "bg-red-500 animate-pulse"}`} />
                        <span className="text-[10px] font-bold text-brand-400 uppercase tracking-widest">
                          {status === "generating" ? "Processing" : status === "completed" ? "Done" : "Live Streaming"}
                        </span>
                      </div>
                    </div>

                    {liveTranscript.length === 0 ? (
                      <div className="flex flex-col items-center justify-center flex-1 text-brand-300 gap-4 min-h-[200px]">
                        <div className="relative">
                          <Loader2 className="animate-spin" size={32} />
                          <Activity className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-brand-400" size={14} />
                        </div>
                        <p className="text-sm font-medium tracking-wide">
                          {status === "generating" ? "Generating meeting documents..." : "Listening to audio..."}
                        </p>
                      </div>
                    ) : (
                      <div className="flex flex-col gap-4">
                        {liveTranscript.map((item, idx) => (
                          <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            key={idx}
                            className="flex flex-col w-full"
                          >
                            {item.speaker && (
                              <div className="flex items-center gap-2 mb-2">
                                <span className="text-[10px] font-black uppercase tracking-[0.2em] text-brand-400">
                                  {item.speaker}
                                </span>
                                <div className="h-px flex-1 bg-brand-100" />
                              </div>
                            )}
                            
                            {item.translatedText ? (
                              // 2-Column or Stacked Bilingual Layout
                              <div className="flex flex-col md:grid md:grid-cols-2 gap-2 md:gap-4 w-full">
                                {/* Source (English) */}
                                <div className={`p-3 md:p-4 rounded-2xl ${item.start === -1 ? 'bg-zinc-50 border border-zinc-100' : 'bg-zinc-100/50'}`}>
                                  <div className="text-[10px] font-mono text-zinc-400 mb-1.5">
                                    {formatTimestamp(item.start)} {item.start === -1 && " (Typing...)"}
                                  </div>
                                  <p className={`text-[13px] md:text-sm leading-relaxed ${item.start === -1 ? "text-zinc-400 italic" : "text-zinc-600"}`}>
                                    {item.text}
                                  </p>
                                </div>
                                {/* Translated (Korean) */}
                                <div className="bg-white p-3 md:p-4 rounded-2xl border border-brand-100 shadow-sm border-l-4 border-l-brand-400">
                                   <div className="text-[10px] font-mono text-zinc-400 mb-1.5 md:hidden">
                                    Translation
                                  </div>
                                  <p className="text-[15px] md:text-base font-semibold leading-relaxed text-brand-900">
                                    {item.translatedText}
                                  </p>
                                </div>
                              </div>
                            ) : (
                              // Monolingual Single Layout
                              <div className={`p-3 md:p-5 rounded-2xl bg-white border border-brand-100 shadow-sm border-l-4 ${item.start === -1 ? 'border-l-zinc-300' : 'border-l-brand-400'}`}>
                                <div className="text-[10px] font-mono text-zinc-400 mb-1.5">
                                  {formatTimestamp(item.start)} {item.start === -1 && " (Typing...)"}
                                </div>
                                <p className={`text-[15px] md:text-lg font-medium leading-relaxed ${item.start === -1 ? "text-brand-400 italic" : "text-brand-900"}`}>
                                  {item.text}
                                </p>
                              </div>
                            )}
                          </motion.div>
                        ))}
                        <div ref={transcriptEndRef} className="h-4" />
                      </div>
                    )}
                  </div>

                  {/* Controls */}
                  {status === "recording" && (
                    <div className="flex flex-col items-center gap-4 md:gap-6 shrink-0 pb-4">
                      {/* Audio Level Wave */}
                      <div className="flex gap-1.5 md:gap-2 items-end h-10 md:h-16">
                        {[...Array(24)].map((_, i) => (
                          <motion.div
                            key={i}
                            animate={{
                              height: isPaused ? 4 : [4, Math.random() * 40 + 6, 4],
                              opacity: isPaused ? 0.3 : [0.3, 1, 0.3],
                            }}
                            transition={{ repeat: Infinity, duration: 0.6, delay: i * 0.03 }}
                            className={`w-1 md:w-1.5 rounded-full transition-colors ${isPaused ? "bg-brand-300" : "bg-brand-900"}`}
                          />
                        ))}
                      </div>

                      <div className="flex items-center gap-6 md:gap-8">
                        <button
                          onClick={pauseRecording}
                          className={`w-14 h-14 md:w-16 md:h-16 rounded-2xl flex items-center justify-center transition-all shadow-xl ${
                            isPaused ? "bg-emerald-500 hover:bg-emerald-600" : "bg-amber-500 hover:bg-amber-600"
                          } text-white hover:scale-105 active:scale-95`}
                        >
                          {isPaused ? <Play size={24} fill="currentColor" /> : <Pause size={24} fill="currentColor" />}
                        </button>
                        <button
                          onClick={stopRecording}
                          className="w-20 h-20 md:w-24 md:h-24 bg-brand-950 text-white rounded-[1.5rem] md:rounded-[2rem] flex items-center justify-center hover:bg-brand-900 transition-all shadow-2xl hover:scale-105 active:scale-95 group relative overflow-hidden"
                        >
                          <Square size={28} fill="currentColor" className="group-hover:scale-90 transition-transform relative z-10" />
                        </button>
                      </div>

                      <p className="text-[10px] md:text-xs text-brand-400 font-bold uppercase tracking-[0.3em] animate-pulse">
                        {isPaused ? "Recording Paused" : "Session in progress"}
                      </p>
                    </div>
                  )}

                  {status === "generating" && (
                    <div className="flex items-center gap-3 text-amber-600">
                      <Loader2 className="animate-spin" size={20} />
                      <span className="text-sm font-bold">AI is generating meeting documents...</span>
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
                    <p className="text-lg font-bold text-zinc-900">Start New Session</p>
                    <p className="text-sm text-zinc-500 mt-1">
                      Real-time transcription via OpenAI Realtime API
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

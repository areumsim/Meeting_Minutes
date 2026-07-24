import React, { useState, useEffect, lazy, Suspense } from "react";
import { Mic, FileAudio, List, Settings, FileText, MessageCircleQuestion, ClipboardList, HelpCircle, Network, CalendarClock, Loader2 } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import Dashboard from "./components/Dashboard";
import Onboarding from "./components/Onboarding";
// 초기 로딩 번들을 줄이기 위해 대시보드(기본 화면)·온보딩 외 뷰는 지연 로드(code splitting).
const Recorder = lazy(() => import("./components/Recorder"));
const SessionDetail = lazy(() => import("./components/SessionDetail"));
const FileUpload = lazy(() => import("./components/FileUpload"));
const TextInput = lazy(() => import("./components/TextInput"));
const WikiAsk = lazy(() => import("./components/WikiAsk"));
const PrepBrief = lazy(() => import("./components/PrepBrief"));
const Help = lazy(() => import("./components/Help"));
const SettingsView = lazy(() => import("./components/Settings"));
const GraphExplorer = lazy(() => import("./components/GraphExplorer"));
const Assistant = lazy(() => import("./components/Assistant"));
import { getApiKey, getConfig, isPackagedMode } from "./lib/api";

type View = "dashboard" | "recorder" | "upload" | "text" | "wiki" | "prep" | "assistant" | "graph" | "help" | "detail" | "settings";

export default function App() {
  const [viewState, setViewState] = useState<View>("dashboard");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);
  const [ffmpegMissing, setFfmpegMissing] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);

  const view = viewState;
  const setView = (v: View) => {
    if ((window as any).isRecordingActive) {
      if (!window.confirm("실시간 녹음이 진행 중입니다. 나가면 녹음이 중지됩니다. 계속할까요?")) {
        return;
      }
      (window as any).stopActiveRecording && (window as any).stopActiveRecording();
    }
    setViewState(v);
  };

  // 최초 실행 유도(OpenAI 키/Obsidian 볼트 미설정) + ffmpeg 상태 확인
  useEffect(() => {
    (async () => {
      try {
        const res = await fetch("/api/health");
        if (res.ok) {
          const h = await res.json();
          setFfmpegMissing(h.ffmpeg_available === false);
        }
      } catch { /* 백엔드 없음(모바일) — 무시 */ }

      try {
        const packaged = await isPackagedMode();
        const cfg = await getConfig();
        const hasKey = !!(cfg?.api?.openai_api_key) || !!getApiKey();
        const dismissed = localStorage.getItem("ONBOARDING_DISMISSED") === "1";
        // 패키지(exe) 모드 + OpenAI 키 미설정 + 아직 마법사를 닫지 않았으면 온보딩 표시.
        if (packaged && !hasKey && !dismissed) {
          setShowOnboarding(true);
        } else if (!hasKey) {
          // 그 외(키 없음)엔 최소한 설정 화면으로 유도.
          setView("settings");
        }
      } catch {
        if (!getApiKey()) setView("settings");
      }
    })();

    // [설정]의 '설정 마법사 다시 열기' 버튼에서 발생시키는 이벤트.
    const openHandler = () => setShowOnboarding(true);
    window.addEventListener("mm:open-onboarding", openHandler);
    return () => window.removeEventListener("mm:open-onboarding", openHandler);
  }, []);

  const navigateToDetail = (id: string) => {
    setSelectedSessionId(id);
    setView("detail");
  };

  return (
    <div className="min-h-[100dvh] bg-brand-50 text-brand-950 font-sans selection:bg-emerald-100 flex flex-col md:flex-row pb-[calc(env(safe-area-inset-bottom,0px)+4rem)] md:pb-0">

      {/* 첫 실행 설정 마법사 */}
      {showOnboarding && <Onboarding onClose={() => { setShowOnboarding(false); setView("settings"); }} />}

      {/* Sidebar (iPad / Desktop) */}
      <nav className="hidden md:flex fixed left-0 top-0 bottom-0 w-64 bg-white border-r border-brand-200 flex-col z-50 pt-[env(safe-area-inset-top,0px)] shadow-xl shadow-brand-900/5">
        <div className="p-8">
          <div className="flex items-center gap-3 mb-12">
            <div className="w-10 h-10 bg-brand-950 rounded-xl flex items-center justify-center text-white shadow-lg shadow-brand-900/20">
              <Mic size={20} />
            </div>
            <h1 className="font-sans font-bold text-xl tracking-tight">AI Minutes</h1>
          </div>

          <div className="space-y-2">
            <NavItem icon={<List size={18} />} label="대시보드" active={view === "dashboard"} onClick={() => setView("dashboard")} />
            <NavItem icon={<Mic size={18} />} label="녹음" active={view === "recorder"} onClick={() => setView("recorder")} />
            <NavItem icon={<FileAudio size={18} />} label="업로드" active={view === "upload"} onClick={() => setView("upload")} />
            <NavItem icon={<FileText size={18} />} label="텍스트 분석" active={view === "text"} onClick={() => setView("text")} />
            <NavItem icon={<MessageCircleQuestion size={18} />} label="위키 질문" active={view === "wiki"} onClick={() => setView("wiki")} />
            <NavItem icon={<ClipboardList size={18} />} label="회의 준비" active={view === "prep"} onClick={() => setView("prep")} />
            <NavItem icon={<CalendarClock size={18} />} label="회의 비서" active={view === "assistant"} onClick={() => setView("assistant")} />
            <NavItem icon={<Network size={18} />} label="지식그래프" active={view === "graph"} onClick={() => setView("graph")} />
            <NavItem icon={<HelpCircle size={18} />} label="도움말" active={view === "help"} onClick={() => setView("help")} />
          </div>
        </div>

        <div className="mt-auto p-8 border-t border-brand-100">
          <NavItem icon={<Settings size={18} />} label="설정" active={view === "settings"} onClick={() => setView("settings")} />
        </div>
      </nav>

      {/* Bottom Tab Bar (iPhone / Mobile) */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-brand-200 z-50 flex items-center justify-around pb-[env(safe-area-inset-bottom,0px)] pt-1 px-1 shadow-[0_-10px_30px_rgba(0,0,0,0.05)]">
        <TabItem icon={<List size={20} />} label="홈" active={view === "dashboard"} onClick={() => setView("dashboard")} />
        <TabItem icon={<Mic size={20} />} label="녹음" active={view === "recorder"} onClick={() => setView("recorder")} />
        <TabItem icon={<FileAudio size={20} />} label="업로드" active={view === "upload"} onClick={() => setView("upload")} />
        <TabItem icon={<FileText size={20} />} label="텍스트" active={view === "text"} onClick={() => setView("text")} />
        <TabItem icon={<MessageCircleQuestion size={20} />} label="위키" active={view === "wiki"} onClick={() => setView("wiki")} />
        <TabItem icon={<Settings size={20} />} label="설정" active={view === "settings"} onClick={() => setView("settings")} />
      </nav>

      {/* Main Content */}
      <main className="flex-1 w-full md:ml-64 p-4 md:p-8 lg:p-12 pt-[calc(env(safe-area-inset-top,0px)+1rem)] relative">
        {ffmpegMissing && (
          <div className="mb-4 rounded-xl border border-amber-300 bg-amber-50 text-amber-800 px-4 py-3 text-sm">
            ⚠️ <strong>ffmpeg가 설치되어 있지 않습니다.</strong> 오디오 파일 업로드/변환 기능이 동작하지 않을 수 있습니다.
            프로그램 폴더의 <code className="font-mono">vendor/ffmpeg/</code> 에 <code className="font-mono">ffmpeg.exe</code>를 넣거나 시스템 PATH에 ffmpeg를 추가하세요.
          </div>
        )}
        <AnimatePresence mode="wait">
          <motion.div
            key={view + (selectedSessionId || "")}
            initial={{ opacity: 0, scale: 0.98, y: 5 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -5 }}
            transition={{ duration: 0.2 }}
            className="h-full"
          >
            <Suspense fallback={<div className="flex items-center justify-center py-32"><Loader2 className="animate-spin text-brand-400" size={32} /></div>}>
              {view === "dashboard" && <Dashboard onSelectSession={navigateToDetail} onNewUpload={() => setView("upload")} onNewRecord={() => setView("recorder")} />}
              {view === "recorder" && <Recorder onComplete={navigateToDetail} onExit={() => setView("dashboard")} />}
              {view === "upload" && <FileUpload onComplete={navigateToDetail} />}
              {view === "text" && <TextInput onComplete={navigateToDetail} />}
              {view === "wiki" && <WikiAsk />}
              {view === "prep" && <PrepBrief onSaved={navigateToDetail} />}
              {view === "assistant" && <Assistant />}
              {view === "graph" && <GraphExplorer />}
              {view === "help" && <Help />}
              {view === "settings" && <SettingsView />}
              {view === "detail" && selectedSessionId && (
                <SessionDetail id={selectedSessionId} onBack={() => setView("dashboard")} />
              )}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

function NavItem({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`w-full flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-300 group ${
        active
          ? "bg-brand-900 text-white font-semibold shadow-lg shadow-brand-900/10"
          : "text-brand-500 hover:bg-brand-100 hover:text-brand-900"
      }`}
    >
      <span className={`transition-transform duration-300 ${active ? "scale-110" : "group-hover:scale-110"}`}>
        {icon}
      </span>
      <span className="text-sm">{label}</span>
    </button>
  );
}

function TabItem({ icon, label, active, onClick }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`flex-1 flex flex-col items-center justify-center pt-2 pb-1 gap-1 rounded-2xl transition-all duration-300 relative ${
        active ? "text-brand-900" : "text-brand-400 hover:text-brand-600"
      }`}
    >
      <div className={`p-1.5 rounded-xl transition-all duration-300 ${active ? "bg-brand-100 scale-110" : ""}`}>
        {icon}
      </div>
      <span className={`text-[10px] font-medium transition-all duration-300 ${active ? "font-bold" : ""}`}>{label}</span>
    </button>
  );
}

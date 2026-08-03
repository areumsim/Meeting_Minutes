import React, { useState, useEffect, lazy, Suspense } from "react";
import { Mic, FileAudio, List, Settings, FileText, MessageCircleQuestion, ClipboardList, HelpCircle, Network, CalendarClock, Loader2, PanelLeftClose, PanelLeftOpen } from "lucide-react";
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
  // config.json 을 읽지 못한 상태. 이때는 서버가 저장을 막으므로(기존 설정 보호)
  // 사용자에게 반드시 알려야 한다 — 포터블은 콘솔이 없어 stderr 경고가 안 보인다.
  const [configError, setConfigError] = useState<string | null>(null);
  const [recovering, setRecovering] = useState(false);
  const [showOnboarding, setShowOnboarding] = useState(false);
  const [graphQuery, setGraphQuery] = useState("");   // 위키링크로 지식그래프 진입 시 초기 검색어
  // 데스크톱 사이드바 접기/펴기 (localStorage로 상태 유지)
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem("SIDEBAR_COLLAPSED") === "1");
  const toggleCollapsed = () => setCollapsed((c) => {
    const n = !c;
    try { localStorage.setItem("SIDEBAR_COLLAPSED", n ? "1" : "0"); } catch { /* ignore */ }
    return n;
  });

  // 손상된 config 에서 빠져나오기. 어느 쪽이든 손상 파일은 지우지 않고 보관한다.
  const recoverConfig = async (restoreBackup: boolean) => {
    if (!window.confirm(restoreBackup
      ? "마지막 정상 설정(config.json.bak)으로 되돌립니다. 손상된 파일은 지우지 않고 따로 보관합니다. 계속할까요?"
      : "손상된 설정을 따로 보관하고 빈 설정으로 시작합니다. API 키를 다시 입력해야 합니다. 계속할까요?")) return;
    setRecovering(true);
    try {
      const res = await fetch("/api/config/recover", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ restore_backup: restoreBackup }),
      });
      const r = await res.json().catch(() => ({}));
      if (res.ok && r.ok) {
        setConfigError(null);
        alert(r.message || "복구했습니다.");
        window.location.reload();       // 설정이 바뀌었으니 화면 전체를 다시 읽는다
      } else {
        alert(r.message || r.detail || "복구에 실패했습니다.");
      }
    } catch (e: any) {
      alert(`복구에 실패했습니다: ${e?.message || e}`);
    } finally {
      setRecovering(false);
    }
  };

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
          setConfigError(h.config_error || null);
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

  // 위키링크/노드 클릭 → 지식 그래프로 이동(해당 대상을 자동 검색·펼침).
  const navigateToGraph = (query?: string) => {
    setGraphQuery(query || "");
    setView("graph");
  };
  // 사이드바에서 '지식그래프'를 직접 누른 경우: 이전 검색어를 비우고 상위 노드 목록부터.
  const openGraphNav = () => {
    setGraphQuery("");
    setView("graph");
  };

  return (
    <div className="min-h-[100dvh] bg-brand-50 text-brand-950 font-sans selection:bg-emerald-100 flex flex-col md:flex-row pb-[calc(env(safe-area-inset-bottom,0px)+4rem)] md:pb-0">

      {/* 첫 실행 설정 마법사 */}
      {showOnboarding && <Onboarding onClose={() => { setShowOnboarding(false); setView("settings"); }} />}

      {/* Sidebar (iPad / Desktop) */}
      <nav className={`hidden md:flex fixed left-0 top-0 bottom-0 ${collapsed ? "w-20" : "w-64"} bg-white border-r border-brand-200 flex-col z-50 pt-[env(safe-area-inset-top,0px)] shadow-xl shadow-brand-900/5 transition-[width] duration-200`}>
        <div className={collapsed ? "px-3 py-6" : "p-8"}>
          <div className={`flex items-center ${collapsed ? "flex-col gap-2 mb-6" : "gap-3 mb-12"}`}>
            <div className="w-10 h-10 bg-brand-950 rounded-xl flex items-center justify-center text-white shadow-lg shadow-brand-900/20 shrink-0">
              <Mic size={20} />
            </div>
            {!collapsed && <h1 className="font-sans font-bold text-xl tracking-tight">AI Minutes</h1>}
            <button
              onClick={toggleCollapsed}
              title={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
              aria-label={collapsed ? "사이드바 펼치기" : "사이드바 접기"}
              className={`${collapsed ? "" : "ml-auto"} text-brand-400 hover:text-brand-900 p-1.5 rounded-lg hover:bg-brand-100 transition-colors`}
            >
              {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            </button>
          </div>

          <div className="space-y-2">
            <NavItem collapsed={collapsed} icon={<List size={18} />} label="대시보드" active={view === "dashboard"} onClick={() => setView("dashboard")} />
            <NavItem collapsed={collapsed} icon={<Mic size={18} />} label="녹음" active={view === "recorder"} onClick={() => setView("recorder")} />
            <NavItem collapsed={collapsed} icon={<FileAudio size={18} />} label="업로드" active={view === "upload"} onClick={() => setView("upload")} />
            <NavItem collapsed={collapsed} icon={<FileText size={18} />} label="텍스트 분석" active={view === "text"} onClick={() => setView("text")} />
            <NavItem collapsed={collapsed} icon={<MessageCircleQuestion size={18} />} label="위키 질문" active={view === "wiki"} onClick={() => setView("wiki")} />
            <NavItem collapsed={collapsed} icon={<ClipboardList size={18} />} label="회의 준비" active={view === "prep"} onClick={() => setView("prep")} />
            <NavItem collapsed={collapsed} icon={<CalendarClock size={18} />} label="회의 비서" active={view === "assistant"} onClick={() => setView("assistant")} />
            <NavItem collapsed={collapsed} icon={<Network size={18} />} label="지식그래프" active={view === "graph"} onClick={openGraphNav} />
            <NavItem collapsed={collapsed} icon={<HelpCircle size={18} />} label="도움말" active={view === "help"} onClick={() => setView("help")} />
          </div>
        </div>

        <div className={`mt-auto ${collapsed ? "px-3 py-6" : "p-8"} border-t border-brand-100`}>
          <NavItem collapsed={collapsed} icon={<Settings size={18} />} label="설정" active={view === "settings"} onClick={() => setView("settings")} />
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
      <main className={`flex-1 w-full ${collapsed ? "md:ml-20" : "md:ml-64"} p-4 md:p-8 lg:p-12 pt-[calc(env(safe-area-inset-top,0px)+1rem)] relative transition-[margin] duration-200`}>
        {configError && (
          <div className="mb-4 rounded-xl border border-red-300 bg-red-50 text-red-800 px-4 py-3 text-sm">
            <div>
              ⛔ <strong>설정 파일(config.json)을 읽지 못했습니다.</strong> ({configError})
            </div>
            <div className="mt-1">
              기본값으로 동작 중이며, 기존 설정을 보호하기 위해 <strong>설정 저장이 차단</strong>돼 있습니다.
              파일을 직접 고치거나 아래에서 복구하세요.
            </div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button onClick={() => recoverConfig(true)} disabled={recovering}
                className="px-3 py-1.5 rounded-lg bg-red-100 font-semibold hover:bg-red-200 disabled:opacity-50">
                마지막 정상 설정으로 되돌리기
              </button>
              <button onClick={() => recoverConfig(false)} disabled={recovering}
                className="px-3 py-1.5 rounded-lg bg-red-100 font-semibold hover:bg-red-200 disabled:opacity-50">
                보관하고 새로 시작
              </button>
            </div>
          </div>
        )}
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
              {view === "graph" && <GraphExplorer initialQuery={graphQuery} />}
              {view === "help" && <Help onNavigate={setView} />}
              {view === "settings" && <SettingsView />}
              {view === "detail" && selectedSessionId && (
                <SessionDetail id={selectedSessionId} onBack={() => setView("dashboard")} onOpenGraph={navigateToGraph} />
              )}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  );
}

function NavItem({ icon, label, active, onClick, collapsed }: { icon: React.ReactNode; label: string; active: boolean; onClick: () => void; collapsed?: boolean }) {
  return (
    <button
      onClick={onClick}
      title={collapsed ? label : undefined}
      className={`w-full flex items-center ${collapsed ? "justify-center px-2" : "gap-3 px-4"} py-3 rounded-xl transition-all duration-300 group ${
        active
          ? "bg-brand-900 text-white font-semibold shadow-lg shadow-brand-900/10"
          : "text-brand-500 hover:bg-brand-100 hover:text-brand-900"
      }`}
    >
      <span className={`transition-transform duration-300 ${active ? "scale-110" : "group-hover:scale-110"}`}>
        {icon}
      </span>
      {!collapsed && <span className="text-sm">{label}</span>}
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

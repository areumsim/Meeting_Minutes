import React, { useState, useEffect } from "react";
import { Mic, FileAudio, List, Settings, FileText } from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import Dashboard from "./components/Dashboard";
import Recorder from "./components/Recorder";
import SessionDetail from "./components/SessionDetail";
import FileUpload from "./components/FileUpload";
import TextInput from "./components/TextInput";
import SettingsView from "./components/Settings";
import { getApiKey } from "./lib/api";

type View = "dashboard" | "recorder" | "upload" | "text" | "detail" | "settings";

export default function App() {
  const [viewState, setViewState] = useState<View>("dashboard");
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  const view = viewState;
  const setView = (v: View) => {
    if ((window as any).isRecordingActive) {
      if (!window.confirm("A realtime recording is currently in progress. Exiting will stop the recording. Are you sure?")) {
        return;
      }
      (window as any).stopActiveRecording && (window as any).stopActiveRecording();
    }
    setViewState(v);
  };

  // Initial guard: need OpenAI API Key
  useEffect(() => {
    if (!getApiKey()) {
      setView("settings");
    }
  }, []);

  const navigateToDetail = (id: string) => {
    setSelectedSessionId(id);
    setView("detail");
  };

  return (
    <div className="min-h-[100dvh] bg-brand-50 text-brand-950 font-sans selection:bg-emerald-100 flex flex-col md:flex-row pb-[calc(env(safe-area-inset-bottom,0px)+4rem)] md:pb-0">
      
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
            <NavItem icon={<List size={18} />} label="Dashboard" active={view === "dashboard"} onClick={() => setView("dashboard")} />
            <NavItem icon={<Mic size={18} />} label="Record" active={view === "recorder"} onClick={() => setView("recorder")} />
            <NavItem icon={<FileAudio size={18} />} label="Upload" active={view === "upload"} onClick={() => setView("upload")} />
            <NavItem icon={<FileText size={18} />} label="Text Analysis" active={view === "text"} onClick={() => setView("text")} />
          </div>
        </div>

        <div className="mt-auto p-8 border-t border-brand-100">
          <NavItem icon={<Settings size={18} />} label="Settings" active={view === "settings"} onClick={() => setView("settings")} />
        </div>
      </nav>

      {/* Bottom Tab Bar (iPhone / Mobile) */}
      <nav className="md:hidden fixed bottom-0 left-0 right-0 bg-white/90 backdrop-blur-xl border-t border-brand-200 z-50 flex items-center justify-around pb-[env(safe-area-inset-bottom,0px)] pt-1 px-1 shadow-[0_-10px_30px_rgba(0,0,0,0.05)]">
        <TabItem icon={<List size={20} />} label="Home" active={view === "dashboard"} onClick={() => setView("dashboard")} />
        <TabItem icon={<Mic size={20} />} label="Record" active={view === "recorder"} onClick={() => setView("recorder")} />
        <TabItem icon={<FileAudio size={20} />} label="Upload" active={view === "upload"} onClick={() => setView("upload")} />
        <TabItem icon={<FileText size={20} />} label="Text" active={view === "text"} onClick={() => setView("text")} />
        <TabItem icon={<Settings size={20} />} label="Settings" active={view === "settings"} onClick={() => setView("settings")} />
      </nav>

      {/* Main Content */}
      <main className="flex-1 w-full md:ml-64 p-4 md:p-8 lg:p-12 pt-[calc(env(safe-area-inset-top,0px)+1rem)] relative">
        <AnimatePresence mode="wait">
          <motion.div
            key={view + (selectedSessionId || "")}
            initial={{ opacity: 0, scale: 0.98, y: 5 }}
            animate={{ opacity: 1, scale: 1, y: 0 }}
            exit={{ opacity: 0, scale: 0.98, y: -5 }}
            transition={{ duration: 0.2 }}
            className="h-full"
          >
            {view === "dashboard" && <Dashboard onSelectSession={navigateToDetail} onNewUpload={() => setView("upload")} onNewRecord={() => setView("recorder")} />}
            {view === "recorder" && <Recorder onComplete={navigateToDetail} />}
            {view === "upload" && <FileUpload onComplete={navigateToDetail} />}
            {view === "text" && <TextInput onComplete={navigateToDetail} />}
            {view === "settings" && <SettingsView />}
            {view === "detail" && selectedSessionId && (
              <SessionDetail id={selectedSessionId} onBack={() => setView("dashboard")} />
            )}
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

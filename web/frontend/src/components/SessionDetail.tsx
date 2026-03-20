import React, { useState, useEffect, useRef } from "react";
import { Share as ShareIcon, ArrowLeft, Copy, Download, Loader2, CheckCircle, Clock,
  FileText, List, Zap, AlertCircle, RefreshCw, Send
} from "lucide-react";
import { motion } from "motion/react";
import { Share } from '@capacitor/share';
import { getSession, getSessionStatus, generateSummaryForSession, getTargetEmail } from "../lib/api";
import { formatDuration, formatTime } from "../lib/format";
import type { Session, Segment, Document as Doc } from "../lib/types";

interface Props {
  id: string;
  onBack: () => void;
}

type Tab = "script" | "minutes" | "summary" | "actions" | "refined_script";

export default function SessionDetail({ id, onBack }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [activeTab, setActiveTab] = useState<Tab>("minutes");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [userNotes, setUserNotes] = useState("");
  const [regenerating, setRegenerating] = useState(false);

  const load = async () => {
    try {
      const data = await getSession(id);
      setSession(data.session);
      setSegments(data.segments || []);
      setDocuments(data.documents || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, [id]);

  // 처리 중이면 폴링 (session을 dependency에서 제외하여 무한 재시작 방지)
  useEffect(() => {
    const t = setInterval(async () => {
      try {
        const s = await getSessionStatus(id);
        if (s.status !== "processing") load();
      } catch { /* ignore */ }
    }, 3000);
    return () => clearInterval(t);
  }, [id]);

  const getDoc = (type: string) => documents.find(d => d.type === type);
  const activeDoc = getDoc(activeTab);

  const copyTimerRef = React.useRef<ReturnType<typeof setTimeout>>(undefined);

  const handleCopy = () => {
    if (!activeDoc?.content) return;
    navigator.clipboard.writeText(activeDoc.content);
    setCopied(true);
    clearTimeout(copyTimerRef.current);
    copyTimerRef.current = setTimeout(() => setCopied(false), 2000);
  };

  // cleanup copy timer on unmount
  useEffect(() => () => clearTimeout(copyTimerRef.current), []);

  const handleDownload = () => {
    if (!activeDoc?.content) return;
    const ext = activeDoc.format === "json" ? "json" : "md";
    const blob = new Blob([activeDoc.content], { type: "text/plain" });
    const url = URL.createObjectURL(blob);
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = `${session?.title || "document"}_${activeTab}.${ext}`;
      a.click();
    } finally {
      URL.revokeObjectURL(url);
    }
  };

  const handleShare = async () => {
    if (!activeDoc?.content) return;
    try {
      const targetEmail = getTargetEmail();
      const emailQuery = targetEmail ? `?emails=${encodeURIComponent(targetEmail)}` : "";
      
      await Share.share({
        title: session?.title || "Meeting Document",
        text: activeDoc.content,
        url: targetEmail ? `mailto:${targetEmail}?subject=${encodeURIComponent(session?.title || "Meeting Document")}&body=${encodeURIComponent(activeDoc.content)}` : undefined,
        dialogTitle: "Share Document",
      });
    } catch (e) {
      console.error(e);
      // Fallback
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    await generateSummaryForSession(id, userNotes);
    setUserNotes("");
    setRegenerating(false);
    load();
  };

  const tabs: { key: Tab; label: string; icon: React.ReactNode }[] = [
    { key: "minutes", label: "Minutes", icon: <FileText size={14} /> },
    { key: "summary", label: "Summary", icon: <Zap size={14} /> },
    { key: "script", label: "Script", icon: <List size={14} /> },
    { key: "actions", label: "Actions", icon: <CheckCircle size={14} /> },
    { key: "refined_script", label: "Refined", icon: <FileText size={14} /> },
  ];

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32">
        <Loader2 className="animate-spin text-brand-400" size={32} />
      </div>
    );
  }

  if (!session) {
    return (
      <div className="text-center py-20">
        <AlertCircle size={48} className="mx-auto text-red-400 mb-4" />
        <p className="text-lg font-bold text-brand-500">Session not found</p>
        <button onClick={onBack} className="mt-4 text-brand-500 hover:text-brand-900 font-medium">Go back</button>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center gap-4 mb-8">
        <button onClick={onBack} className="p-2 hover:bg-brand-100 rounded-xl transition-colors">
          <ArrowLeft size={20} className="text-brand-500" />
        </button>
        <div className="flex-1">
          <h2 className="text-2xl font-bold tracking-tight">{session.title || "Untitled"}</h2>
          <div className="flex items-center gap-4 mt-1 text-sm text-brand-500">
            <span className="flex items-center gap-1">
              {session.status === "completed" ? <CheckCircle size={14} className="text-emerald-500" /> :
               session.status === "processing" ? <Loader2 size={14} className="text-amber-500 animate-spin" /> :
               <AlertCircle size={14} className="text-red-500" />}
              {session.status}
            </span>
            <span>{session.type}</span>
            {session.duration_sec > 0 && <span>{formatDuration(session.duration_sec)}</span>}
            {session.translate ? <span className="text-amber-600">Translated</span> : null}
            {session.source === "cli" && <span className="text-zinc-400">CLI</span>}
          </div>
        </div>
        <button onClick={load} className="p-2 hover:bg-brand-100 rounded-xl transition-colors">
          <RefreshCw size={16} className="text-brand-400" />
        </button>
      </div>

      {session.status === "processing" ? (
        <div className="bg-white border border-brand-200 rounded-3xl p-16 text-center">
          <Loader2 size={48} className="mx-auto text-amber-500 animate-spin mb-6" />
          <h3 className="text-xl font-bold mb-2">Processing in progress...</h3>
          <p className="text-brand-500">AI is generating your meeting documents. This page will update automatically.</p>
        </div>
      ) : (
        <div className="bg-white border border-brand-200 rounded-3xl shadow-xl overflow-hidden">
          {/* Tabs */}
          <div className="flex border-b border-brand-200 overflow-x-auto scrollbar-hide">
            {tabs.filter(t => getDoc(t.key)).map(t => (
              <button
                key={t.key}
                onClick={() => setActiveTab(t.key)}
                className={`flex items-center gap-2 px-4 md:px-6 py-3 md:py-4 text-xs md:text-sm font-bold transition-all border-b-2 whitespace-nowrap shrink-0 ${
                  activeTab === t.key
                    ? "border-brand-900 text-brand-900"
                    : "border-transparent text-brand-400 hover:text-brand-700"
                }`}
              >
                {t.icon} {t.label}
              </button>
            ))}
          </div>

          {/* Content */}
          <div className="p-8">
            {activeDoc ? (
              <>
                <div className="flex justify-end gap-2 mb-6">
                  <button
                    onClick={handleCopy}
                    className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
                  >
                    {copied ? <CheckCircle size={14} className="text-emerald-500" /> : <Copy size={14} />}
                    {copied ? "Copied!" : "Copy"}
                  </button>
                  <button
                    onClick={handleDownload}
                    className="hidden md:flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
                  >
                    <Download size={14} /> Download
                  </button>
                  <button
                    onClick={handleShare}
                    className="flex items-center gap-2 px-4 py-2 bg-brand-900 text-white rounded-xl text-sm font-medium hover:bg-brand-950 transition-all shadow-md"
                  >
                    <ShareIcon size={14} /> Share
                  </button>
                </div>

                {activeTab === "script" && segments.length > 0 ? (
                  <div className="space-y-4 max-h-[600px] overflow-y-auto">
                    {segments.map((seg, i) => (
                      <div key={i} className="flex gap-4 group">
                        <span className="text-xs text-brand-400 font-mono mt-1 shrink-0 w-14">
                          {formatTime(seg.start_time)}
                        </span>
                        <div className="flex-1">
                          {seg.speaker && (
                            <span className="text-[10px] font-black uppercase tracking-[0.2em] text-brand-400 block mb-1">
                              {seg.speaker}
                            </span>
                          )}
                          <p className="text-brand-800 font-medium leading-relaxed">{seg.text}</p>
                          {seg.translated_text && seg.translated_text !== seg.text && (
                            <p className="text-sm text-amber-700 mt-1 pl-4 border-l-2 border-amber-200">{seg.translated_text}</p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="prose prose-zinc max-w-none max-h-[600px] overflow-y-auto">
                    {activeDoc.format === "json" ? (
                      <pre className="bg-zinc-50 p-6 rounded-xl text-sm overflow-x-auto">
                        {(() => { try { return JSON.stringify(JSON.parse(activeDoc.content), null, 2); } catch { return activeDoc.content; } })()}
                      </pre>
                    ) : (
                      <div className="whitespace-pre-wrap font-medium text-brand-800 leading-relaxed">
                        {activeDoc.content}
                      </div>
                    )}
                  </div>
                )}
                
                {/* Regenerate Section for Summary/Minutes */}
                {(activeTab === "summary" || activeTab === "minutes") && (
                  <div className="mt-12 pt-8 border-t border-zinc-200">
                    <h4 className="text-sm font-bold text-zinc-900 mb-3 flex items-center gap-2">
                      <RefreshCw size={14} className="text-brand-500" /> Regenerate with Notes
                    </h4>
                    <p className="text-xs text-zinc-500 mb-4">Add specific notes, corrections, or instructions to regenerate this document.</p>
                    <textarea
                      value={userNotes}
                      onChange={(e) => setUserNotes(e.target.value)}
                      placeholder="e.g. Please format the action items as a table, and focus more on the Q&A segment."
                      className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none text-sm min-h-[100px] resize-y mb-4"
                    />
                    <button
                      onClick={handleRegenerate}
                      disabled={regenerating || !userNotes.trim()}
                      className="flex items-center gap-2 px-5 py-2.5 bg-black text-white rounded-xl text-sm font-bold disabled:opacity-50 transition-all hover:bg-brand-900"
                    >
                      {regenerating ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />} 
                      {regenerating ? "Regenerating..." : "Regenerate AI Document"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <div className="text-center py-16 text-brand-400">
                <FileText size={32} className="mx-auto mb-4" />
                <p>No {activeTab} document available.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

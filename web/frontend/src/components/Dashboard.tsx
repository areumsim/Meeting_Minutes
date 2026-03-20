import React, { useState, useEffect } from "react";
import {
  Plus, Mic, Search, Trash2, Loader2, FileAudio,
  CheckCircle, AlertCircle, Clock, ChevronRight, RefreshCw,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getSessions, deleteSession, clearSessions } from "../lib/api";
import { formatDate, formatDuration, typeColor } from "../lib/format";
import type { Session } from "../lib/types";

interface Props {
  onSelectSession: (id: string) => void;
  onNewUpload: () => void;
  onNewRecord: () => void;
}

export default function Dashboard({ onSelectSession, onNewUpload, onNewRecord }: Props) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [loading, setLoading] = useState(true);

  const load = async () => {
    setLoading(true);
    try {
      const data = await getSessions(search, typeFilter);
      setSessions(data);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => { load(); }, [search, typeFilter]);

  // 처리 중인 세션 폴링 (dependency에 sessions를 넣지 않아 무한 재시작 방지)
  useEffect(() => {
    const t = setInterval(() => {
      setSessions(prev => {
        if (prev.some(s => s.status === "processing")) load();
        return prev;
      });
    }, 5000);
    return () => clearInterval(t);
  }, [search, typeFilter]);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("Delete this session?")) return;
    await deleteSession(id);
    load();
  };

  const handleClearAll = async () => {
    if (!confirm("Delete ALL session history?")) return;
    await clearSessions();
    load();
  };

  const statusIcon = (s: string) => {
    switch (s) {
      case "completed": return <CheckCircle size={14} className="text-emerald-500" />;
      case "processing": return <Loader2 size={14} className="text-amber-500 animate-spin" />;
      case "error": return <AlertCircle size={14} className="text-red-500" />;
      default: return <Clock size={14} className="text-zinc-400" />;
    }
  };

  return (
    <div className="max-w-5xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-10">
        <div>
          <h2 className="text-3xl font-bold tracking-tight">Dashboard</h2>
          <p className="text-brand-500 mt-1">{sessions.length} sessions</p>
        </div>
        <div className="flex gap-3">
          <button onClick={onNewRecord} className="flex items-center gap-2 px-5 py-3 bg-brand-950 text-white rounded-xl font-semibold hover:bg-brand-900 transition-all shadow-lg hover:scale-105 active:scale-95">
            <Mic size={16} /> Record
          </button>
          <button onClick={onNewUpload} className="flex items-center gap-2 px-5 py-3 bg-white border border-brand-200 text-brand-700 rounded-xl font-semibold hover:bg-brand-50 transition-all hover:scale-105 active:scale-95">
            <Plus size={16} /> Upload
          </button>
        </div>
      </div>

      {/* Search & Filter */}
      <div className="flex flex-col gap-3 mb-8">
        <div className="flex gap-3">
          <div className="flex-1 relative">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-400" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search sessions..."
              className="w-full pl-11 pr-4 py-3 bg-white border border-brand-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none transition-all font-medium"
            />
          </div>
          <button onClick={load} className="px-4 py-3 bg-white border border-brand-200 rounded-xl hover:bg-brand-50 transition-all shrink-0">
            <RefreshCw size={16} className="text-brand-500" />
          </button>
        </div>
        <div className="flex gap-3">
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="flex-1 px-4 py-3 bg-white border border-brand-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none font-medium"
          >
            <option value="">All Types</option>
            <option value="meeting">Meeting</option>
            <option value="seminar">Seminar</option>
            <option value="lecture">Lecture</option>
          </select>
          {sessions.length > 0 && (
            <button onClick={handleClearAll} className="px-4 py-3 bg-white border border-red-200 text-red-500 rounded-xl hover:bg-red-50 transition-all text-sm font-medium shrink-0">
              Clear All
            </button>
          )}
        </div>
      </div>

      {/* Session List */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-brand-400">
          <Loader2 className="animate-spin" size={24} />
        </div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-20">
          <FileAudio size={48} className="mx-auto text-brand-300 mb-4" />
          <p className="text-lg font-bold text-brand-500">No sessions yet</p>
          <p className="text-sm text-brand-400 mt-1">Start a recording or upload a file to begin.</p>
        </div>
      ) : (
        <div className="grid gap-4">
          <AnimatePresence>
            {sessions.map((s) => (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                onClick={() => onSelectSession(s.id)}
                className="group bg-white border border-brand-200 rounded-2xl p-6 hover:shadow-lg hover:border-brand-300 transition-all cursor-pointer"
              >
                <div className="flex items-center gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="font-bold text-brand-900 truncate">{s.title || "Untitled"}</h3>
                      <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full ${typeColor(s.type)}`}>
                        {s.type}
                      </span>
                      {s.source === "cli" && (
                        <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-500">CLI</span>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-xs text-brand-400">
                      <span className="flex items-center gap-1">
                        {statusIcon(s.status)}
                        {s.status}
                      </span>
                      <span>{formatDate(s.date || s.created_at)}</span>
                      {s.duration_sec > 0 && <span>{formatDuration(s.duration_sec)}</span>}
                      {s.translate ? <span className="text-amber-600">Translated</span> : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      onClick={(e) => handleDelete(s.id, e)}
                      className="p-2 text-brand-300 hover:text-red-500 md:opacity-0 md:group-hover:opacity-100 transition-all"
                    >
                      <Trash2 size={16} />
                    </button>
                    <ChevronRight size={16} className="text-brand-300 group-hover:text-brand-500 transition-colors" />
                  </div>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
        </div>
      )}
    </div>
  );
}

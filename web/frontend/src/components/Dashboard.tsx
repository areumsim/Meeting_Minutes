import React, { useState, useEffect, useRef } from "react";
import {
  Plus, Mic, Search, Trash2, Loader2, FileAudio,
  CheckCircle, AlertCircle, Clock, ChevronRight, RefreshCw, Undo2, XCircle,
} from "lucide-react";
import { motion, AnimatePresence } from "motion/react";
import { getSessions, deleteSession, clearSessions, getTrash, restoreSession, purgeSession } from "../lib/api";
import CostSummary from "./CostSummary";
import { formatDate, formatDuration, typeColor, typeLabel, statusLabel } from "../lib/format";
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
  // 조회 실패를 '세션이 없음'과 구분한다 — 과거엔 console.error 만 하고 빈 상태를
  // 그려서, 백엔드가 죽은 것과 회의가 하나도 없는 것이 화면상 똑같았다.
  const [loadError, setLoadError] = useState<string>("");
  // 휴지통 보기. 삭제가 soft delete 로 바뀌었으므로 되돌릴 자리가 화면에 있어야 한다.
  const [showTrash, setShowTrash] = useState(false);
  const [notice, setNotice] = useState<{ text: string; undoId?: string } | null>(null);
  // 응답이 순서 뒤바뀌어 도착해도 마지막 요청의 결과만 반영한다.
  const reqSeqRef = useRef(0);

  const load = async (background = false) => {
    // background 갱신은 로딩 스피너로 목록을 깜빡이지 않는다
    if (!background) setLoading(true);
    const seq = ++reqSeqRef.current;
    try {
      const data = showTrash ? await getTrash() : await getSessions(search, typeFilter);
      if (seq !== reqSeqRef.current) return;   // 낡은 응답 폐기
      setSessions(data);
      setLoadError("");
    } catch (e) {
      if (seq !== reqSeqRef.current) return;
      console.error(e);
      setLoadError(e instanceof Error ? e.message : String(e));
    }
    if (!background && seq === reqSeqRef.current) setLoading(false);
  };

  // 검색어는 디바운스한다 — 한글은 자모마다 change 가 떠서 "회의록" 입력에도
  // 요청이 7회 이상 나갔다. 유형 필터는 클릭이라 즉시 반영한다.
  useEffect(() => {
    const t = setTimeout(() => { load(); }, search ? 300 : 0);
    return () => clearTimeout(t);
  }, [search, typeFilter, showTrash]);

  // 처리 중인 세션 폴링 — setState updater 안에서 load()를 호출하면 StrictMode에서
  // 이중 실행되는 부수효과가 생기므로 ref로 현재 목록을 읽는다.
  const sessionsRef = useRef<Session[]>([]);
  useEffect(() => { sessionsRef.current = sessions; }, [sessions]);
  useEffect(() => {
    const t = setInterval(() => {
      if (sessionsRef.current.some(s => s.status === "processing")) load(true);
    }, 5000);
    return () => clearInterval(t);
  }, [search, typeFilter]);

  const handleDelete = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("이 회의를 휴지통으로 보낼까요? 나중에 되돌릴 수 있습니다.")) return;
    const r = await deleteSession(id);
    // 되돌릴 수 있다는 사실을 알려야 휴지통이 의미가 있다(예전에는 회복 불가였다).
    setNotice(r.restorable
      ? { text: "휴지통으로 보냈습니다.", undoId: id }
      : { text: "삭제했습니다." });
    load();
  };

  const handleClearAll = async () => {
    if (!confirm("모든 회의 기록을 휴지통으로 보낼까요? 나중에 되돌릴 수 있습니다.")) return;
    await clearSessions();
    setNotice({ text: "모두 휴지통으로 보냈습니다. [휴지통]에서 되돌릴 수 있습니다." });
    load();
  };

  const handleRestore = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await restoreSession(id);
      setNotice({ text: "되돌렸습니다." });
      load();
    } catch (err) {
      setNotice({ text: err instanceof Error ? err.message : String(err) });
    }
  };

  const handlePurge = async (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!confirm("완전히 삭제할까요? 회의록·전사 폴더는 Windows 휴지통으로 보냅니다.")) return;
    try {
      const r = await purgeSession(id);
      setNotice({ text: r.message || "완전히 삭제했습니다." });
      load();
    } catch (err) {
      setNotice({ text: err instanceof Error ? err.message : String(err) });
    }
  };

  const statusIcon = (s: string) => {
    switch (s) {
      case "completed": return <CheckCircle size={14} className="text-emerald-500" />;
      case "processing": return <Loader2 size={14} className="text-amber-500 animate-spin" />;
      case "error": return <AlertCircle size={14} className="text-red-500" />;
      default: return <Clock size={14} className="text-zinc-500" />;
    }
  };

  return (
    <div className="max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div>
          <h2 className="text-2xl font-bold tracking-tight">대시보드</h2>
          <p className="text-sm text-brand-500 mt-0.5">
            {showTrash ? `휴지통 ${sessions.length}개` : `세션 ${sessions.length}개`}
          </p>
        </div>
        <div className="flex gap-2">
          <button onClick={onNewRecord} className="flex items-center gap-2 px-4 py-2.5 bg-brand-950 text-white rounded-xl font-semibold hover:bg-brand-900 transition-all shadow-lg active:scale-95">
            <Mic size={16} /> 녹음
          </button>
          <button onClick={onNewUpload} className="flex items-center gap-2 px-4 py-2.5 bg-white border border-brand-200 text-brand-700 rounded-xl font-semibold hover:bg-brand-50 transition-all active:scale-95">
            <Plus size={16} /> 업로드
          </button>
        </div>
      </div>

      {/* 비용 요약 — 조회 실패 시 스스로 렌더하지 않는다(세션 목록에 영향 없음) */}
      <CostSummary />

      {/* Search & Filter */}
      <div className="flex flex-col gap-2 mb-4">
        <div className="flex gap-3">
          <div className="flex-1 relative">
            <Search size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-brand-500" />
            <input
              type="text"
              aria-label="세션 검색"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="세션 검색..."
              className="w-full pl-11 pr-4 py-2.5 bg-white border border-brand-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none transition-all font-medium text-sm"
            />
          </div>
          <button
            onClick={() => load()}
            title="목록 새로고침"
            aria-label="세션 목록 새로고침"
            className="px-4 py-3 bg-white border border-brand-200 rounded-xl hover:bg-brand-50 transition-all shrink-0"
          >
            <RefreshCw size={16} className="text-brand-500" />
          </button>
        </div>
        <div className="flex gap-3">
          <select
            value={typeFilter}
            onChange={(e) => setTypeFilter(e.target.value)}
            className="flex-1 px-4 py-2.5 bg-white border border-brand-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none font-medium text-sm"
          >
            <option value="">전체 유형</option>
            <option value="meeting">회의</option>
            <option value="seminar">세미나</option>
            <option value="lecture">강의</option>
          </select>
          <button
            onClick={() => { setNotice(null); setShowTrash(v => !v); }}
            className={`px-4 py-2.5 rounded-xl border transition-all text-sm font-medium shrink-0 ${
              showTrash ? "bg-brand-950 text-white border-brand-950" : "bg-white border-brand-200 text-brand-700 hover:bg-brand-50"}`}
          >
            {showTrash ? "목록으로" : "휴지통"}
          </button>
          {!showTrash && sessions.length > 0 && (
            <button onClick={handleClearAll} className="px-4 py-2.5 bg-white border border-red-200 text-red-500 rounded-xl hover:bg-red-50 transition-all text-sm font-medium shrink-0">
              전체 삭제
            </button>
          )}
        </div>
      </div>

      {/* 삭제/복구 결과 알림 — 되돌릴 수 있다는 사실을 여기서 알린다. */}
      {notice && (
        <div className="mb-4 flex items-center justify-between gap-3 rounded-xl border border-brand-200 bg-brand-50 px-4 py-2.5 text-sm">
          <span className="text-brand-800">{notice.text}</span>
          <div className="flex items-center gap-2 shrink-0">
            {notice.undoId && (
              <button
                onClick={(e) => { const id = notice.undoId!; setNotice(null); handleRestore(id, e); }}
                className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-white border border-brand-300 font-semibold hover:bg-brand-100"
              >
                <Undo2 size={14} /> 되돌리기
              </button>
            )}
            <button onClick={() => setNotice(null)} aria-label="알림 닫기" className="text-brand-500 hover:text-brand-700">
              <XCircle size={16} />
            </button>
          </div>
        </div>
      )}
      {showTrash && (
        <p className="mb-3 text-xs text-brand-500">
          휴지통의 회의는 목록에 보이지 않지만 그대로 남아 있습니다. [완전 삭제]를 누르면
          회의록·전사 폴더를 Windows 휴지통으로 보냅니다.
        </p>
      )}

      {/* Session List */}
      {loading ? (
        <div className="flex items-center justify-center py-20 text-brand-500">
          <Loader2 className="animate-spin" size={24} />
        </div>
      ) : loadError ? (
        <div role="alert" className="text-center py-20">
          <AlertCircle size={48} className="mx-auto text-red-400 mb-4" />
          <p className="text-lg font-bold text-brand-900">세션 목록을 불러올 수 없습니다</p>
          <p className="text-sm text-brand-500 mt-1">
            서버가 실행 중인지 확인해 주세요. (회의가 없는 것이 아니라 조회에 실패했습니다)
          </p>
          <p className="text-xs text-brand-500 mt-2 break-words max-w-md mx-auto">{loadError}</p>
          <button
            onClick={() => load()}
            className="mt-4 px-4 py-2 bg-brand-900 text-white rounded-xl text-sm font-bold hover:bg-brand-800 transition-colors"
          >
            다시 시도
          </button>
        </div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-20">
          <FileAudio size={48} className="mx-auto text-brand-300 mb-4" />
          <p className="text-lg font-bold text-brand-500">
            {showTrash ? "휴지통이 비어 있습니다" : "아직 세션이 없습니다"}
          </p>
          <p className="text-sm text-brand-500 mt-1">
            {showTrash ? "삭제한 회의가 여기 모입니다." : "녹음을 시작하거나 파일을 업로드해 보세요."}
          </p>
        </div>
      ) : (
        <div className="grid gap-2.5">
          <AnimatePresence>
            {sessions.map((s) => (
              <motion.div
                key={s.id}
                initial={{ opacity: 0, y: 10 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -10 }}
                onClick={() => onSelectSession(s.id)}
                className="group bg-white border border-brand-200 rounded-xl p-3.5 hover:shadow-md hover:border-brand-300 transition-all cursor-pointer"
              >
                <div className="flex items-center gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 mb-1">
                      <h3 className="font-bold text-brand-900 truncate">{s.title || "제목 없음"}</h3>
                      <span className={`text-[11px] font-bold tracking-wider px-2 py-0.5 rounded-full ${typeColor(s.type)}`}>
                        {typeLabel(s.type)}
                      </span>
                      {s.source === "cli" && (
                        <span className="text-[11px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-full bg-zinc-100 text-zinc-500">CLI</span>
                      )}
                    </div>
                    <div className="flex items-center gap-4 text-xs text-brand-500">
                      <span className="flex items-center gap-1">
                        {statusIcon(s.status)}
                        {statusLabel(s.status)}
                      </span>
                      <span>{formatDate(s.date || s.created_at)}</span>
                      {s.duration_sec > 0 && <span>{formatDuration(s.duration_sec)}</span>}
                      {s.translate ? <span className="text-amber-600">번역됨</span> : null}
                    </div>
                    {s.status === "error" && s.error_detail && (
                      <p className="mt-1 text-xs text-red-500 truncate" title={s.error_detail}>
                        {s.error_detail}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {/* hover 로만 드러내면(과거 md:opacity-0) 발견되지 않고, 보이지 않는
                        상태로 키보드 포커스도 받는다. 항상 보이게 두고 탭 타깃을 44px 로. */}
                    {showTrash ? (
                      <>
                        <button
                          onClick={(e) => handleRestore(s.id, e)}
                          title="되돌리기"
                          aria-label={`${s.title || "제목 없음"} 되돌리기`}
                          className="p-3 -m-1 text-brand-500 hover:text-brand-900 transition-colors"
                        >
                          <Undo2 size={16} />
                        </button>
                        <button
                          onClick={(e) => handlePurge(s.id, e)}
                          title="완전 삭제 (폴더는 Windows 휴지통으로)"
                          aria-label={`${s.title || "제목 없음"} 완전 삭제`}
                          className="p-3 -m-1 text-brand-500 hover:text-red-500 transition-colors"
                        >
                          <XCircle size={16} />
                        </button>
                      </>
                    ) : (
                      <button
                        onClick={(e) => handleDelete(s.id, e)}
                        title="휴지통으로 보내기"
                        aria-label={`${s.title || "제목 없음"} 휴지통으로 보내기`}
                        className="p-3 -m-1 text-brand-500 hover:text-red-500 transition-colors"
                      >
                        <Trash2 size={16} />
                      </button>
                    )}
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

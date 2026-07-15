import React, { useState, useEffect, useRef } from "react";
import { Share as ShareIcon, ArrowLeft, Copy, Download, Loader2, CheckCircle, Clock,
  FileText, List, Zap, AlertCircle, RefreshCw, Send, Network
} from "lucide-react";
import { motion } from "motion/react";
import { Share } from '@capacitor/share';
import { getSession, getSessionStatus, generateSummaryForSession, getTargetEmail,
  getSessionGraph, getNodeNeighbors } from "../lib/api";
import { formatDuration, formatTime } from "../lib/format";
import type { Session, Segment, Document as Doc, SessionGraph, GraphNeighbors } from "../lib/types";

interface Props {
  id: string;
  onBack: () => void;
}

type Tab = "script" | "minutes" | "summary" | "actions" | "fact_check" | "wiki_context" | "wiki_proposal" | "refined_script" | "graph";

// 그래프 노드 타입 -> 섹션 표시 라벨 (이 컴포넌트의 다른 탭 라벨과 톤을 맞춰 영문 사용)
const GRAPH_TYPE_LABELS: Record<string, string> = {
  meeting: "회의",
  person: "인물",
  organization: "조직",
  topic: "주제",
  decision: "결정",
  action: "액션",
  note: "노트",
};

export default function SessionDetail({ id, onBack }: Props) {
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [activeTab, setActiveTab] = useState<Tab>("minutes");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [userNotes, setUserNotes] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [graph, setGraph] = useState<SessionGraph | null>(null);
  const [expandedNodeId, setExpandedNodeId] = useState<string | null>(null);
  const [neighborsCache, setNeighborsCache] = useState<Record<string, GraphNeighbors>>({});
  const [neighborsLoading, setNeighborsLoading] = useState<string | null>(null);

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

  const loadGraph = async () => {
    try {
      const g = await getSessionGraph(id);
      setGraph(g && g.node_count > 0 ? g : null);
    } catch {
      // 백엔드가 없는(모바일 전용) 배포에서는 실패가 정상 — 조용히 무시하고 탭을 숨긴다
      setGraph(null);
    }
  };

  useEffect(() => {
    load();
    setGraph(null);
    setExpandedNodeId(null);
    setNeighborsCache({});
    loadGraph();
  }, [id]);

  const handleToggleNeighbors = async (nodeId: string) => {
    if (expandedNodeId === nodeId) {
      setExpandedNodeId(null);
      return;
    }
    setExpandedNodeId(nodeId);
    if (!neighborsCache[nodeId]) {
      setNeighborsLoading(nodeId);
      try {
        const result = await getNodeNeighbors(nodeId, { depth: 1 });
        setNeighborsCache(prev => ({ ...prev, [nodeId]: result }));
      } catch (e) {
        console.error(e);
      } finally {
        setNeighborsLoading(null);
      }
    }
  };

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
    { key: "minutes", label: "회의록", icon: <FileText size={14} /> },
    { key: "summary", label: "요약", icon: <Zap size={14} /> },
    { key: "fact_check", label: "사실확인", icon: <AlertCircle size={14} /> },
    { key: "script", label: "스크립트", icon: <List size={14} /> },
    { key: "actions", label: "액션", icon: <CheckCircle size={14} /> },
    { key: "wiki_context", label: "위키 맥락", icon: <FileText size={14} /> },
    { key: "wiki_proposal", label: "위키 제안", icon: <FileText size={14} /> },
    { key: "refined_script", label: "정제본", icon: <FileText size={14} /> },
    { key: "graph", label: "그래프", icon: <Network size={14} /> },
  ];

  const isTabAvailable = (t: Tab) => (t === "graph" ? !!graph : !!getDoc(t));

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
        <p className="text-lg font-bold text-brand-500">세션을 찾을 수 없습니다</p>
        <button onClick={onBack} className="mt-4 text-brand-500 hover:text-brand-900 font-medium">돌아가기</button>
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
          <h2 className="text-2xl font-bold tracking-tight">{session.title || "제목 없음"}</h2>
          <div className="flex items-center gap-4 mt-1 text-sm text-brand-500">
            <span className="flex items-center gap-1">
              {session.status === "completed" ? <CheckCircle size={14} className="text-emerald-500" /> :
               session.status === "processing" ? <Loader2 size={14} className="text-amber-500 animate-spin" /> :
               <AlertCircle size={14} className="text-red-500" />}
              {({ completed: "완료", processing: "처리 중", error: "오류" } as Record<string, string>)[session.status] || session.status}
            </span>
            <span>{({ meeting: "회의", seminar: "세미나", lecture: "강의" } as Record<string, string>)[session.type] || session.type}</span>
            {session.duration_sec > 0 && <span>{formatDuration(session.duration_sec)}</span>}
            {session.translate ? <span className="text-amber-600">번역됨</span> : null}
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
          <h3 className="text-xl font-bold mb-2">처리 중입니다...</h3>
          <p className="text-brand-500">AI가 회의 문서를 생성하고 있습니다. 이 화면은 자동으로 갱신됩니다.</p>
        </div>
      ) : (
        <div className="bg-white border border-brand-200 rounded-3xl shadow-xl overflow-hidden">
          {/* Tabs */}
          <div className="flex border-b border-brand-200 overflow-x-auto scrollbar-hide">
            {tabs.filter(t => isTabAvailable(t.key)).map(t => (
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
            {activeTab === "graph" ? (
              graph ? (
                <div className="space-y-8 max-h-[600px] overflow-y-auto">
                  {Object.entries(graph.nodes).map(([type, nodes]) => (
                    <div key={type}>
                      <h4 className="text-xs font-black uppercase tracking-[0.2em] text-brand-400 mb-3">
                        {GRAPH_TYPE_LABELS[type] || type} ({nodes.length})
                      </h4>
                      <div className="flex flex-wrap gap-2">
                        {nodes.map(node => (
                          <div key={node.id} className="flex flex-col">
                            <button
                              onClick={() => handleToggleNeighbors(node.id)}
                              className={`px-3 py-1.5 rounded-xl text-sm font-medium transition-all border ${
                                expandedNodeId === node.id
                                  ? "bg-brand-900 text-white border-brand-900"
                                  : "bg-brand-50 text-brand-700 border-brand-200 hover:bg-brand-100"
                              }`}
                            >
                              {node.label}
                            </button>
                            {expandedNodeId === node.id && (
                              <div className="mt-2 mb-1 px-4 py-3 bg-zinc-50 rounded-xl border border-zinc-200 text-sm max-w-sm">
                                {neighborsLoading === node.id ? (
                                  <div className="flex items-center gap-2 text-brand-400">
                                    <Loader2 size={14} className="animate-spin" /> Loading...
                                  </div>
                                ) : neighborsCache[node.id]?.neighbors.length ? (
                                  <ul className="space-y-1">
                                    {neighborsCache[node.id].neighbors.map(n => (
                                      <li key={n.id} className="text-brand-700">
                                        <span className="text-brand-400">{GRAPH_TYPE_LABELS[n.type] || n.type}:</span> {n.label}
                                      </li>
                                    ))}
                                  </ul>
                                ) : (
                                  <span className="text-brand-400">연결된 노드가 없습니다.</span>
                                )}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="text-center py-16 text-brand-400">
                  <Network size={32} className="mx-auto mb-4" />
                  <p>그래프 데이터가 없습니다.</p>
                </div>
              )
            ) : activeDoc ? (
              <>
                <div className="flex justify-end gap-2 mb-6">
                  <button
                    onClick={handleCopy}
                    className="flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
                  >
                    {copied ? <CheckCircle size={14} className="text-emerald-500" /> : <Copy size={14} />}
                    {copied ? "복사됨!" : "복사"}
                  </button>
                  <button
                    onClick={handleDownload}
                    className="hidden md:flex items-center gap-2 px-4 py-2 bg-brand-50 text-brand-700 rounded-xl text-sm font-medium hover:bg-brand-100 transition-all"
                  >
                    <Download size={14} /> 다운로드
                  </button>
                  <button
                    onClick={handleShare}
                    className="flex items-center gap-2 px-4 py-2 bg-brand-900 text-white rounded-xl text-sm font-medium hover:bg-brand-950 transition-all shadow-md"
                  >
                    <ShareIcon size={14} /> 공유
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
                      <RefreshCw size={14} className="text-brand-500" /> 노트 반영해 재생성
                    </h4>
                    <p className="text-xs text-zinc-500 mb-4">수정 사항이나 지시를 추가해 이 문서를 다시 생성합니다.</p>
                    <textarea
                      value={userNotes}
                      onChange={(e) => setUserNotes(e.target.value)}
                      placeholder="예: 액션 아이템을 표로 정리하고, Q&A 부분을 더 자세히 다뤄주세요."
                      className="w-full px-4 py-3 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-brand-900 outline-none text-sm min-h-[100px] resize-y mb-4"
                    />
                    <button
                      onClick={handleRegenerate}
                      disabled={regenerating || !userNotes.trim()}
                      className="flex items-center gap-2 px-5 py-2.5 bg-black text-white rounded-xl text-sm font-bold disabled:opacity-50 transition-all hover:bg-brand-900"
                    >
                      {regenerating ? <Loader2 size={16} className="animate-spin" /> : <RefreshCw size={16} />}
                      {regenerating ? "재생성 중..." : "AI 문서 재생성"}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <div className="text-center py-16 text-brand-400">
                <FileText size={32} className="mx-auto mb-4" />
                <p>해당 문서가 없습니다.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

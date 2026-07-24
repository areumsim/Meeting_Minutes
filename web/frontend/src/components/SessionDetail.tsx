import React, { useState, useEffect, useRef } from "react";
import { Share as ShareIcon, ArrowLeft, Copy, Download, Loader2, CheckCircle, Clock,
  FileText, List, Zap, AlertCircle, RefreshCw, Send, Network
} from "lucide-react";
import { motion } from "motion/react";
import Markdown from "./Markdown";
import { Share } from '@capacitor/share';
import { getSession, getSessionStatus, generateSummaryForSession, getTargetEmail,
  getSessionGraph, getNodeNeighbors, getUploadProgress, getSessionCost, cancelUpload,
  mirrorServerSession, type SessionCost } from "../lib/api";
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
  const [progress, setProgress] = useState<{ percent: number; stage: string; elapsed: number } | null>(null);
  const [cost, setCost] = useState<SessionCost | null>(null);
  const [cancelling, setCancelling] = useState(false);

  const handleCancel = async () => {
    if (!window.confirm("처리를 취소하시겠습니까? 현재 단계가 끝나면 중단되고 이 세션은 삭제됩니다.")) return;
    setCancelling(true);
    const r = await cancelUpload(id);
    if (!r.ok) {
      setCancelling(false);
      alert(r.message || "취소할 수 없습니다.");
      return;
    }
    // 성공: 백그라운드가 다음 단계 경계에서 중단·세션 삭제. 목록으로 복귀한다
    // (STT 등 긴 단계 실행 중이면 그 단계가 끝난 뒤 실제 중단됨).
    onBack();
  };

  const load = async (tryMirror = true) => {
    try {
      const data = await getSession(id);
      setSession(data.session);
      setSegments(data.segments || []);
      setDocuments(data.documents || []);
      if (data.session?.status === "completed") {
        getSessionCost(id).then((c) => { if (c?.ok) setCost(c); });
      }
    } catch (e) {
      // 로컬 IndexedDB에 없어도 서버에는 있을 수 있다(완료 직후 미러 실패 등).
      // 서버에서 한 번 미러링해 재시도 — '세션을 찾을 수 없습니다' 막다른 화면 방지.
      if (tryMirror) {
        try {
          const ok = await mirrorServerSession(id);
          if (ok) return load(false);
        } catch { /* 서버 미가용 */ }
      }
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

  // 처리 중일 때만 폴링 — 완료/오류 세션에서 2초마다 서버를 두드리지 않는다.
  // (regenerating 중에는 서버 상태가 다시 processing이 될 수 있어 함께 폴링)
  const shouldPoll = session?.status === "processing" || regenerating;
  useEffect(() => {
    if (!shouldPoll) return;
    const t = setInterval(async () => {
      try {
        const s = await getSessionStatus(id);
        if (s.status !== "processing") { setProgress(null); load(); return; }
        const p = await getUploadProgress(id);
        if (p.found) setProgress({ percent: p.percent ?? 0, stage: p.stage ?? "", elapsed: p.elapsed ?? 0 });
      } catch { /* ignore */ }
    }, 2000);
    return () => clearInterval(t);
  }, [id, shouldPoll]);

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
      // mailto URL은 길이 제한이 있어 본문이 길면 잘리거나 실패한다 — 앞부분만 싣는다.
      const mailBody = activeDoc.content.length > 1800
        ? activeDoc.content.slice(0, 1800) + "\n\n…(전문은 앱에서 '다운로드'로 저장해 첨부하세요)"
        : activeDoc.content;
      await Share.share({
        title: session?.title || "회의 문서",
        text: activeDoc.content,
        url: targetEmail ? `mailto:${targetEmail}?subject=${encodeURIComponent(session?.title || "회의 문서")}&body=${encodeURIComponent(mailBody)}` : undefined,
        dialogTitle: "문서 공유",
      });
    } catch (e: any) {
      console.error(e);
      // 사용자가 공유 시트를 닫은 경우(AbortError)는 조용히 무시
      if (e?.name !== "AbortError" && !String(e?.message || "").includes("cancel")) {
        alert("공유에 실패했습니다. '복사' 또는 '다운로드'를 이용해주세요.");
      }
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await generateSummaryForSession(id, userNotes);
      setUserNotes("");
      // 패키지 모드에선 요청이 즉시 반환되고 서버가 백그라운드로 재생성한다 —
      // 상태가 processing에서 벗어날 때까지 대기 표시를 유지한다(최대 5분).
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const s = await getSessionStatus(id).catch(() => null);
        if (!s || s.status !== "processing") break;
      }
    } catch (e: any) {
      alert(e?.message || "재생성 요청에 실패했습니다.");
    } finally {
      setRegenerating(false);
      load();
    }
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

  // 현재 탭 문서가 없으면 첫 번째 사용 가능한 탭으로 자동 전환 —
  // 기본값(minutes)이 없을 때 '해당 문서가 없습니다'만 보이는 것 방지.
  useEffect(() => {
    if (loading) return;
    if (!isTabAvailable(activeTab)) {
      const first = tabs.find(t => isTabAvailable(t.key));
      if (first) setActiveTab(first.key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, documents, graph]);

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
        <p className="text-sm text-brand-400 mt-2">서버에서 아직 동기화 중일 수 있어요. 잠시 후 다시 시도해주세요.</p>
        <div className="mt-4 flex items-center justify-center gap-4">
          <button
            onClick={() => { setLoading(true); load(); }}
            className="px-4 py-2 bg-brand-900 text-white rounded-xl text-sm font-semibold hover:bg-brand-950 transition-all"
          >
            다시 불러오기
          </button>
          <button onClick={onBack} className="text-brand-500 hover:text-brand-900 font-medium">돌아가기</button>
        </div>
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
            <span>{({ meeting: "회의", seminar: "세미나", lecture: "강의", prep: "회의 준비" } as Record<string, string>)[session.type] || session.type}</span>
            {session.duration_sec > 0 && <span>{formatDuration(session.duration_sec)}</span>}
            {session.translate ? <span className="text-amber-600">번역됨</span> : null}
            {cost && typeof cost.total === "number" && (
              <span
                className="flex items-center gap-1 text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-md font-medium"
                title={`STT $${cost.stt} + 번역 $${cost.translate} + 회의록 $${cost.minutes} (${cost.stt_model}) · 대략치`}
              >
                💵 예상 ${cost.total?.toFixed(3)}
              </span>
            )}
            {session.source === "cli" && <span className="text-zinc-400">CLI</span>}
          </div>
          {session.status === "error" && session.error_detail && (
            <p className="mt-2 text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
              {session.error_detail}
            </p>
          )}
        </div>
        <button onClick={() => load()} className="p-2 hover:bg-brand-100 rounded-xl transition-colors">
          <RefreshCw size={16} className="text-brand-400" />
        </button>
      </div>

      {session.status === "processing" ? (
        <div className="bg-white border border-brand-200 rounded-3xl p-12 md:p-16 text-center">
          <Loader2 size={48} className="mx-auto text-amber-500 animate-spin mb-6" />
          <h3 className="text-xl font-bold mb-2">처리 중입니다...</h3>
          <p className="text-brand-500 mb-6">AI가 회의 문서를 생성하고 있습니다. 이 화면은 자동으로 갱신됩니다.</p>
          {progress && (
            <div className="max-w-md mx-auto">
              <div className="flex items-center justify-between text-sm mb-2">
                <span className="font-semibold text-brand-700">{progress.stage || "처리 중"}</span>
                <span className="font-mono text-brand-500">{progress.percent}%</span>
              </div>
              <div className="h-2.5 bg-brand-100 rounded-full overflow-hidden">
                {/* STT 단계는 내부 진행률이 없어 퍼센트가 한동안 멈춘 것처럼 보인다.
                    실제로는 동작 중임을 알리도록 이동 애니메이션(pulse)을 겹쳐 보여준다. */}
                <div
                  className={`h-full bg-emerald-500 rounded-full transition-all duration-500 ${progress.percent < 100 ? "animate-pulse" : ""}`}
                  style={{ width: `${Math.max(3, progress.percent)}%` }}
                />
              </div>
              <p className="text-xs text-brand-400 mt-2">
                경과 {formatDuration(progress.elapsed)} · 오디오 길이·서버 상황에 따라 수 분 걸릴 수 있습니다.
                {progress.stage.includes("STT") || progress.stage.includes("음성 인식")
                  ? " (음성 인식은 파일 길이에 비례해 가장 오래 걸리며, 이 구간에서는 퍼센트가 잠시 멈춘 것처럼 보일 수 있습니다.)"
                  : ""}
              </p>
            </div>
          )}
          <button
            onClick={handleCancel}
            disabled={cancelling}
            className="mt-8 inline-flex items-center gap-2 px-5 py-2.5 bg-red-50 text-red-600 rounded-xl text-sm font-semibold hover:bg-red-100 transition-all disabled:opacity-50"
          >
            {cancelling ? <Loader2 size={16} className="animate-spin" /> : <AlertCircle size={16} />}
            {cancelling ? "취소 중..." : "처리 취소"}
          </button>
        </div>
      ) : (
        <div className="bg-white border border-brand-200 rounded-3xl shadow-xl overflow-hidden">
          {/* 회의 준비(prep) 세션은 브리핑 문서 1개만 생성되므로 스크립트·사실확인 등
              다른 탭이 없다. 사용자가 "탭이 비었다"고 오해하지 않도록 안내한다. */}
          {session.type === "prep" && (
            <div className="px-6 py-3 bg-brand-50 border-b border-brand-200 text-sm text-brand-600 flex items-center gap-2">
              <FileText size={14} className="shrink-0" />
              회의 준비 브리핑 세션입니다 — 아래 <b>회의록</b> 탭에 브리핑이 들어 있습니다(스크립트·사실확인 등 다른 문서는 생성되지 않습니다).
            </div>
          )}
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
                  <div className="max-h-[600px] overflow-y-auto">
                    {activeDoc.format === "json" ? (
                      <pre className="bg-zinc-50 p-6 rounded-xl text-sm overflow-x-auto">
                        {(() => { try { return JSON.stringify(JSON.parse(activeDoc.content), null, 2); } catch { return activeDoc.content; } })()}
                      </pre>
                    ) : (
                      <Markdown content={activeDoc.content} />
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

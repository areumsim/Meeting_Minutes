import React, { useState, useEffect, useRef } from "react";
import { Share as ShareIcon, ArrowLeft, Copy, Download, Loader2, CheckCircle, Clock,
  FileText, List, Zap, AlertCircle, RefreshCw, Send, Network, BookOpen, ChevronDown
} from "lucide-react";
import { motion } from "motion/react";
import Markdown from "./Markdown";
import MiniGraph from "./MiniGraph";
import { Share } from '@capacitor/share';
import { getSession, getSessionStatus, generateSummaryForSession, getTargetEmail,
  getSessionGraph, getNodeNeighbors, getUploadProgress, getSessionCost, cancelUpload,
  mirrorServerSession, retrySession, getSessionRelatedNotes, type SessionCost,
  type RelatedNoteRow, type RelatedNoteCross } from "../lib/api";
import { formatDuration, formatTime, typeLabel, statusLabel } from "../lib/format";
import type { Session, Segment, Document as Doc, SessionGraph, GraphNeighbors } from "../lib/types";

interface Props {
  id: string;
  onBack: () => void;
  onOpenGraph?: (query?: string) => void;   // 위키링크/노드 클릭 시 지식 그래프로 이동
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

export default function SessionDetail({ id, onBack, onOpenGraph }: Props) {
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
  // 노드별 조회 실패 표시 — '이웃이 없음'과 구분해야 한다(후자는 사실이 아닌 문장이 된다).
  const [neighborsError, setNeighborsError] = useState<Record<string, boolean>>({});
  const [progress, setProgress] = useState<{ percent: number; stage: string; elapsed: number } | null>(null);
  const [cost, setCost] = useState<SessionCost | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // 재시도 직후 STT 재사용 여부 안내(처리 화면 상단 배너). 재과금 가능성을 사용자가 알게 한다.
  const [retryNote, setRetryNote] = useState<string | null>(null);
  // 회의 중 실시간 검색이 참조한 관련 노트(근거 포함) + 교차 회의 집계 (FR-5)
  const [related, setRelated] = useState<RelatedNoteRow[]>([]);
  const [relatedCross, setRelatedCross] = useState<RelatedNoteCross[]>([]);
  const [relatedOpen, setRelatedOpen] = useState(false);

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const r = await retrySession(id);
      // STT 재사용 여부를 알려 재과금 없이 이어짐을 사용자가 알게 한다.
      if (session) setSession({ ...session, status: "processing", error_detail: undefined });
      setRetryNote(
        r.reusedStt === false
          ? "완료된 전사가 없어 음성 인식부터 다시 처리합니다 — API 비용이 다시 발생할 수 있습니다."
          : "완료된 전사를 재사용해 이어서 처리합니다 — 음성 인식 비용은 다시 청구되지 않습니다."
      );
      await load();
    } catch (e) {
      alert(`재시도 실패: ${e instanceof Error ? e.message : "잠시 후 다시 시도하세요."}`);
    } finally {
      setRetrying(false);
    }
  };

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

  const loadRelated = async () => {
    // 관련 노트는 부가 정보 — 실패해도 상세 화면 전체를 막지 않는다.
    const r = await getSessionRelatedNotes(id);
    setRelated(r.notes || []);
    setRelatedCross(r.cross || []);
  };

  useEffect(() => {
    load();
    setGraph(null);
    setExpandedNodeId(null);
    setNeighborsCache({});
    setRelated([]);
    setRelatedCross([]);
    setRelatedOpen(false);
    loadGraph();
    loadRelated();
  }, [id]);

  const handleToggleNeighbors = async (nodeId: string) => {
    if (expandedNodeId === nodeId) {
      setExpandedNodeId(null);
      return;
    }
    setExpandedNodeId(nodeId);
    if (!neighborsCache[nodeId]) {
      setNeighborsLoading(nodeId);
      setNeighborsError(prev => ({ ...prev, [nodeId]: false }));   // 재시도 시 초기화
      try {
        const result = await getNodeNeighbors(nodeId, { depth: 1 });
        setNeighborsCache(prev => ({ ...prev, [nodeId]: result }));
      } catch (e) {
        // 조회 실패를 '이웃이 없음'으로 표시하면 사실이 아닌 문장을 보여주게 된다.
        console.error(e);
        setNeighborsError(prev => ({ ...prev, [nodeId]: true }));
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
        if (s.status !== "processing") { setProgress(null); setRetryNote(null); load(); return; }
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
    <div className="max-w-4xl mx-auto">
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
              {statusLabel(session.status)}
            </span>
            <span>{typeLabel(session.type)}</span>
            {session.duration_sec > 0 && <span>{formatDuration(session.duration_sec)}</span>}
            {session.translate ? <span className="text-amber-600">번역됨</span> : null}
            {cost && typeof cost.total === "number" && (
              <span
                className="flex items-center gap-1 text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-md font-medium"
                title={`STT $${cost.stt} + 번역 $${cost.translate} + 회의록 $${cost.minutes}`
                  // 회의 진행 페르소나는 이 회의가 쓴 돈이지만 세션 비용 테이블에는
                  // 안 들어간다(이중 집계 방지) — 빠뜨리면 상세 금액이 실제보다 적다.
                  + (cost.facilitation ? ` + 회의 진행 페르소나 $${cost.facilitation}(실측)` : "")
                  + ` (${cost.stt_model}) · 대략치`}
              >
                💵 예상 ${cost.total?.toFixed(3)}
              </span>
            )}
            {session.source === "cli" && <span className="text-zinc-400">CLI</span>}
          </div>
          {/* 벤더 전환 고지 — 과거엔 폴백 사실이 노트 frontmatter 에만 남아
              업로드·배치 사용자는 자기 회의 음성이 다른 회사로 갔는지 알 수 없었다. */}
          {session.stt_fallback_used ? (
            <p className="mt-2 text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              <b>대체 경로로 처리되었습니다</b>
              {session.stt_provider ? ` — ${session.stt_provider}` : ""}
              <br />
              기본 음성 인식이 실패해 대체 제공자로 전환했습니다. 이 회의의 음성이 위
              제공자로 전송되었고, 회의록 출처에도 같은 내용이 기록됩니다.
            </p>
          ) : null}
          {session.status === "error" && (
            <div className="mt-2">
              {session.error_detail && (
                <p className="text-sm text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
                  {session.error_detail}
                </p>
              )}
              <button
                onClick={handleRetry}
                disabled={retrying}
                className="mt-2 inline-flex items-center gap-2 px-4 py-2 bg-zinc-900 text-white text-sm font-semibold rounded-xl hover:bg-zinc-800 disabled:opacity-50 transition-colors"
              >
                {retrying ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                {retrying ? "재시도 중..." : "다시 시도"}
              </button>
              <p className="mt-1.5 text-xs text-brand-400">
                음성 인식(STT)이 끝난 뒤 실패한 경우, 재시도는 완료된 전사를 재사용해 비용을 다시 청구하지 않습니다.
              </p>
            </div>
          )}
        </div>
        <button onClick={() => load()} className="p-2 hover:bg-brand-100 rounded-xl transition-colors">
          <RefreshCw size={16} className="text-brand-400" />
        </button>
      </div>

      {session.status === "processing" ? (
        <div className="bg-white border border-brand-200 rounded-3xl p-12 md:p-16 text-center">
          {retryNote && (
            <div className="max-w-md mx-auto mb-6 px-4 py-2.5 bg-brand-50 border border-brand-200 rounded-xl text-sm text-brand-600 text-left flex items-start gap-2">
              <RefreshCw size={15} className="mt-0.5 shrink-0 text-brand-400" />
              <span>{retryNote}</span>
            </div>
          )}
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
                <div className="space-y-8 max-h-[65vh] overflow-y-auto overscroll-contain">
                  {/* 시각적 그래프 개요 — 아래 타입별 목록과 함께 제공 */}
                  {(() => {
                    const allNodes = Object.values(graph.nodes).flat();
                    const meetingId = (graph.nodes.meeting?.[0])?.id;
                    return allNodes.length > 0 ? (
                      <div className="rounded-xl border border-brand-100 bg-brand-50/40">
                        <MiniGraph
                          nodes={allNodes}
                          edges={graph.edges}
                          centerId={meetingId}
                          activeId={expandedNodeId}
                          onNodeClick={(n) => handleToggleNeighbors(n.id)}
                        />
                      </div>
                    ) : null;
                  })()}
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
                                    <Loader2 size={14} className="animate-spin" /> 불러오는 중...
                                  </div>
                                ) : neighborsError[node.id] ? (
                                  <span className="text-red-500">
                                    연결 정보를 불러오지 못했습니다. 닫고 다시 열면 재시도합니다.
                                  </span>
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
                  <div className="space-y-4 max-h-[65vh] overflow-y-auto overscroll-contain">
                    {segments.map((seg, i) => (
                      <div key={i} className="flex gap-4 group">
                        <span className="text-xs text-brand-400 font-mono mt-1 shrink-0 w-14">
                          {formatTime(seg.start_time)}
                        </span>
                        <div className="flex-1 min-w-0">
                          {seg.speaker && (
                            <span className="text-[10px] font-black uppercase tracking-[0.2em] text-brand-400 block mb-1">
                              {seg.speaker}
                            </span>
                          )}
                          <p className="ko-text text-brand-800 font-medium leading-relaxed">{seg.text}</p>
                          {seg.translated_text && seg.translated_text !== seg.text && (
                            <p className="ko-text text-sm text-amber-700 mt-1 pl-4 border-l-2 border-amber-200">{seg.translated_text}</p>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="max-h-[65vh] overflow-y-auto overscroll-contain">
                    {activeDoc.format === "json" ? (
                      <pre className="bg-zinc-50 p-6 rounded-xl text-sm overflow-x-auto">
                        {(() => { try { return JSON.stringify(JSON.parse(activeDoc.content), null, 2); } catch { return activeDoc.content; } })()}
                      </pre>
                    ) : (
                      <Markdown content={activeDoc.content} onWikiLink={onOpenGraph} />
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

      {/* 참조된 관련 노트 (FR-5) — 회의 중 실시간 검색이 찾은 내부 자료를 근거와
          함께 다시 열람한다. 제목을 누르면 지식 그래프/위키로 이동(FR-3).
          교차 회의 집계는 "이 노트가 최근 회의들에서 몇 번 언급됐나"를 보여준다. */}
      {related.length > 0 && (
        <div className="mt-6 bg-white border border-brand-200 rounded-3xl shadow-xl overflow-hidden">
          <button
            onClick={() => setRelatedOpen(v => !v)}
            className="w-full px-6 py-4 flex items-center justify-between hover:bg-brand-50/60 transition-colors"
          >
            <span className="flex items-center gap-2 text-sm font-bold text-brand-900">
              <BookOpen size={15} className="text-emerald-600" />
              참조된 관련 노트 <span className="text-brand-400 font-medium">({related.length})</span>
            </span>
            <ChevronDown size={16} className={`text-brand-400 transition-transform ${relatedOpen ? "" : "-rotate-90"}`} />
          </button>

          {relatedOpen && (
            <div className="px-6 pb-6">
              <p className="text-xs text-brand-400 mb-4">
                회의 중 발화와 관련해 자동으로 찾은 내부 자료입니다(원본 노트는 수정되지 않습니다).
              </p>
              <div className="space-y-3">
                {related.map((n) => (
                  <div key={n.note_path || n.title} className="border border-brand-100 rounded-xl p-3.5">
                    <div className="flex items-baseline gap-2 flex-wrap">
                      <button
                        onClick={() => onOpenGraph?.(n.title)}
                        className="text-sm font-bold text-brand-900 hover:text-emerald-700 hover:underline text-left"
                        title="지식 그래프/위키에서 이 노트 보기"
                      >
                        {n.source_type === "paper" ? "🎓" : n.source_type === "web" ? "🌐" : "📄"}{" "}
                        {n.section_path || n.title}
                      </button>
                      <span className="text-[10px] font-mono text-brand-400 tabular-nums">
                        관련도 {(n.score ?? 0).toFixed(2)}
                        {(n.hits ?? 1) > 1 ? ` · ${n.hits}회 참조` : ""}
                        {n.found_by === "section" ? " · 섹션 일치" : n.found_by === "web" ? " · 웹" : ""}
                      </span>
                    </div>
                    {n.snippet && <p className="text-xs text-brand-600 mt-1.5">{n.snippet}</p>}
                    {n.segment_text && (
                      <p className="text-[11px] text-brand-400 mt-1.5 italic">
                        발화{n.elapsed_sec ? ` (${formatTime(n.elapsed_sec)})` : ""}: {n.segment_text}
                      </p>
                    )}
                    {n.note_path && (
                      <p className="text-[10px] text-brand-300 mt-1 font-mono truncate">{n.note_path}</p>
                    )}
                  </div>
                ))}
              </div>

              {relatedCross.length > 0 && (
                <div className="mt-6 pt-5 border-t border-brand-100">
                  <h5 className="text-[11px] font-black uppercase tracking-[0.2em] text-brand-400 mb-3">
                    최근 회의에서 자주 참조된 노트
                  </h5>
                  <div className="flex flex-wrap gap-2">
                    {relatedCross.map((c) => (
                      <button
                        key={c.note_path || c.title}
                        onClick={() => onOpenGraph?.(c.title)}
                        title={`${c.note_path}${c.last_date ? ` · 최근 ${c.last_date.slice(0, 10)}` : ""}`}
                        className="text-xs bg-brand-50 border border-brand-200 text-brand-700 px-2.5 py-1 rounded-full hover:bg-brand-100 transition-colors"
                      >
                        {c.title} <span className="text-brand-400">· 회의 {c.session_count}건</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

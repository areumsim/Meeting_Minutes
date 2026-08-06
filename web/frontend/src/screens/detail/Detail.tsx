import React, { useState, useEffect, useRef } from "react";
import {
  ArrowLeft, Copy, Download, Share as ShareIcon, RefreshCw, Check, FileText, AlertTriangle,
} from "lucide-react";
import { Share } from "@capacitor/share";
import Markdown from "../../components/Markdown";
import GraphTab from "./GraphTab";
import RelatedNotesPanel from "./RelatedNotesPanel";
import {
  getSession, getSessionStatus, generateSummaryForSession, getTargetEmail, getSessionGraph,
  getUploadProgress, getSessionCost, cancelUpload, mirrorServerSession, retrySession,
  getSessionRelatedNotes, type SessionCost, type RelatedNoteRow, type RelatedNoteCross,
} from "../../lib/api";
import { formatClock, formatTime, typeLabel, typeColor } from "../../lib/format";
import { Button, IconButton } from "../../ui/Button";
import { StatusPill, Tag, statusTone } from "../../ui/StatusPill";
import { QuietBadge, Banner } from "../../ui/Banner";
import Tabs, { type TabItem } from "../../ui/Tabs";
import Inspector from "../../ui/Inspector";
import { Input } from "../../ui/Field";
import { LoadingBlock, ErrorState, EmptyState, ProgressBar } from "../../ui/states";
import { CostMeter, sessionCostItems } from "../../ui/CostMeter";
import type { Session, Segment, Document as Doc, SessionGraph } from "../../lib/types";

/**
 * 회의 상세 (PRD §6.4 — 캐노니컬 detail_v2).
 *
 * AC 는 "본문이 화면 세로의 대부분을 차지한다"이다. 그래서:
 *  - 헤더는 **한 줄**로 압축한다(뒤로·제목·유형·상태·길이·번역·대체처리·비용·새로고침).
 *  - 재생성은 하단 상시 폼이 아니라 툴바 버튼 → **얇은 입력 줄**(기본 접힘)이다.
 *    종전에는 회의록 아래에 제목+설명+큰 textarea 가 늘 펼쳐져 있어 본문을 밀어냈다.
 *  - 관련 노트는 본문 아래가 아니라 **우측 인스펙터**로 간다(§3-4).
 *
 * 재생성은 회의록·요약 탭에서만 나온다 — 원본도 그 두 탭에서만 노출했으므로 기능 손실이
 * 아니다(PRD §14-2).
 */

type Tab = "minutes" | "summary" | "fact_check" | "brief" | "script" | "actions"
  | "wiki_context" | "wiki_proposal" | "refined_script" | "graph";

const TAB_LABEL: { key: Tab; label: string }[] = [
  { key: "minutes", label: "회의록" },
  { key: "summary", label: "요약" },
  { key: "fact_check", label: "사실확인" },
  // 회의 **중** 화면에 떴던 자동 요약. 문서가 있을 때만 노출된다 — 페르소나를 끈 회의에는
  // 탭 자체가 생기지 않는다.
  { key: "brief", label: "중간 정리" },
  { key: "script", label: "스크립트" },
  { key: "actions", label: "액션" },
  { key: "wiki_context", label: "위키 맥락" },
  { key: "wiki_proposal", label: "위키 제안" },
  { key: "refined_script", label: "정제본" },
  { key: "graph", label: "그래프" },
];

/** 노트를 반영해 다시 만들 수 있는 문서 — 원본과 같은 범위를 유지한다. */
const REGENERABLE: Tab[] = ["minutes", "summary"];

export default function Detail({
  id, onBack, onOpenGraph,
}: {
  id: string;
  onBack: () => void;
  onOpenGraph?: (query?: string) => void;
}) {
  const [session, setSession] = useState<Session | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [documents, setDocuments] = useState<Doc[]>([]);
  const [graph, setGraph] = useState<SessionGraph | null>(null);
  const [activeTab, setActiveTab] = useState<Tab>("minutes");
  const [loading, setLoading] = useState(true);
  const [copied, setCopied] = useState(false);
  const [regenOpen, setRegenOpen] = useState(false);
  const [userNotes, setUserNotes] = useState("");
  const [regenerating, setRegenerating] = useState(false);
  const [progress, setProgress] = useState<{ percent: number; stage: string; elapsed: number } | null>(null);
  const [cost, setCost] = useState<SessionCost | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [retrying, setRetrying] = useState(false);
  // 재시도 직후 STT 재사용 여부 안내. 재과금 가능성을 사용자가 알게 한다.
  const [retryNote, setRetryNote] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [related, setRelated] = useState<RelatedNoteRow[]>([]);
  const [relatedCross, setRelatedCross] = useState<RelatedNoteCross[]>([]);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>(undefined);

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
      // 로컬 IndexedDB 에 없어도 서버에는 있을 수 있다(완료 직후 미러 실패 등).
      // 서버에서 한 번 미러링해 재시도 — '세션을 찾을 수 없습니다' 막다른 화면 방지.
      if (tryMirror) {
        try { if (await mirrorServerSession(id)) return load(false); } catch { /* 서버 미가용 */ }
      }
      console.error(e);
    }
    setLoading(false);
  };

  useEffect(() => {
    setLoading(true);
    setGraph(null); setRelated([]); setRelatedCross([]); setCost(null); setRetryNote(null);
    load();
    getSessionGraph(id).then((g) => setGraph(g && g.node_count > 0 ? g : null)).catch(() => setGraph(null));
    getSessionRelatedNotes(id).then((r) => { setRelated(r.notes || []); setRelatedCross(r.cross || []); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  // 처리 중일 때만 폴링 — 완료·오류 세션에서 2초마다 서버를 두드리지 않는다.
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, shouldPoll]);

  const getDoc = (type: string) => documents.find((d) => d.type === type);
  const available = (t: Tab) => (t === "graph" ? !!graph : !!getDoc(t));
  const activeDoc = getDoc(activeTab);

  // 현재 탭 문서가 없으면 첫 번째 사용 가능한 탭으로 자동 전환 — 기본값(회의록)이 없을 때
  // '해당 문서가 없습니다'만 보이는 것을 막는다.
  useEffect(() => {
    if (loading) return;
    if (!available(activeTab)) {
      const first = TAB_LABEL.find((t) => available(t.key));
      if (first) setActiveTab(first.key);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loading, documents, graph]);

  const handleCopy = () => {
    if (!activeDoc?.content) return;
    navigator.clipboard?.writeText(activeDoc.content);
    setCopied(true);
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopied(false), 2000);
  };

  const handleDownload = () => {
    if (!activeDoc?.content) return;
    const ext = activeDoc.format === "json" ? "json" : "md";
    const url = URL.createObjectURL(new Blob([activeDoc.content], { type: "text/plain" }));
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
      const to = getTargetEmail();
      // mailto URL 은 길이 제한이 있어 본문이 길면 잘리거나 실패한다 — 앞부분만 싣는다.
      const body = activeDoc.content.length > 1800
        ? activeDoc.content.slice(0, 1800) + "\n\n…(전문은 [다운로드]로 저장해 첨부하세요)"
        : activeDoc.content;
      await Share.share({
        title: session?.title || "회의 문서",
        text: activeDoc.content,
        url: to ? `mailto:${to}?subject=${encodeURIComponent(session?.title || "회의 문서")}&body=${encodeURIComponent(body)}` : undefined,
        dialogTitle: "문서 공유",
      });
    } catch (e: any) {
      // 사용자가 공유 시트를 닫은 경우(AbortError)는 조용히 무시
      if (e?.name !== "AbortError" && !String(e?.message || "").includes("cancel")) {
        setError("공유에 실패했습니다. [복사] 또는 [다운로드]를 이용해 주세요.");
      }
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await generateSummaryForSession(id, userNotes);
      setUserNotes("");
      setRegenOpen(false);
      // 패키지 모드에선 요청이 즉시 반환되고 서버가 백그라운드로 재생성한다 —
      // 상태가 processing 에서 벗어날 때까지 대기 표시를 유지한다(최대 5분).
      for (let i = 0; i < 150; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const s = await getSessionStatus(id).catch(() => null);
        if (!s || s.status !== "processing") break;
      }
    } catch (e: any) {
      setError(e?.message || "재생성 요청에 실패했습니다.");
    } finally {
      setRegenerating(false);
      load();
    }
  };

  const handleRetry = async () => {
    setRetrying(true);
    try {
      const r = await retrySession(id);
      if (session) setSession({ ...session, status: "processing", error_detail: undefined });
      setRetryNote(r.reusedStt === false
        ? "완료된 전사가 없어 음성 인식부터 다시 처리합니다 — API 비용이 다시 발생할 수 있습니다."
        : "완료된 전사를 재사용해 이어서 처리합니다 — 음성 인식 비용은 다시 청구되지 않습니다.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "재시도에 실패했습니다. 잠시 후 다시 시도하세요.");
    } finally {
      setRetrying(false);
    }
  };

  const handleCancel = async () => {
    if (!window.confirm("처리를 취소하시겠습니까? 현재 단계가 끝나면 중단되고 이 세션은 삭제됩니다.")) return;
    setCancelling(true);
    const r = await cancelUpload(id);
    if (!r.ok) { setCancelling(false); setError(r.message || "취소할 수 없습니다."); return; }
    // 성공: 백그라운드가 다음 단계 경계에서 중단·세션 삭제. 목록으로 복귀한다.
    onBack();
  };

  if (loading) return <LoadingBlock label="회의를 불러오는 중" />;

  if (!session) {
    return (
      <ErrorState
        title="회의를 찾을 수 없습니다"
        detail="서버에서 아직 동기화 중일 수 있어요. 잠시 후 다시 시도해 주세요."
        onRetry={() => { setLoading(true); load(); }}
        retryLabel="다시 불러오기"
      />
    );
  }

  const tabs: TabItem<Tab>[] = TAB_LABEL.filter((t) => available(t.key));

  return (
    <div className="mx-auto flex min-h-0 w-full max-w-6xl flex-1 flex-col">
      {/* ── 헤더(한 줄) ─────────────────────────────────────────── */}
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={onBack}>라이브러리</Button>
        <h2 className="truncate text-lg font-bold tracking-tight">{session.title || "제목 없음"}</h2>
        <Tag tone={typeColor(session.type)}>{typeLabel(session.type)}</Tag>
        <StatusPill tone={statusTone(session.status)} pulse={session.status === "processing"}>
          {session.status === "completed" ? "완료" : session.status === "processing" ? "처리 중"
            : session.status === "error" ? "오류" : session.status}
        </StatusPill>
        {session.duration_sec > 0 && <Tag>{formatClock(session.duration_sec)}</Tag>}
        {!!session.translate && <Tag>번역됨</Tag>}
        {session.source === "cli" && <Tag>CLI</Tag>}

        {/* 벤더 전환 고지 — 과거엔 폴백 사실이 노트 frontmatter 에만 남아 업로드·배치
            사용자는 자기 회의 음성이 다른 회사로 갔는지 알 수 없었다. 상시 배너였던 것을
            칩+팝오버로 줄이되 내용은 그대로 둔다(§8 "대체 처리 칩"). */}
        {!!session.stt_fallback_used && (
          <QuietBadge label="대체 처리" icon={AlertTriangle} title="대체 경로로 처리되었습니다">
            <p>
              기본 음성 인식이 실패해 대체 제공자
              {session.stt_provider ? ` (${session.stt_provider})` : ""}로 전환했습니다.
            </p>
            <p>이 회의의 음성이 위 제공자로 전송되었고, 회의록 출처에도 같은 내용이 기록됩니다.</p>
          </QuietBadge>
        )}

        <div className="flex-1" />
        {cost && typeof cost.total === "number" && (
          <CostMeter compact total={cost.total} items={sessionCostItems(cost as any)} />
        )}
        <IconButton icon={RefreshCw} label="새로고침" size="sm" onClick={() => load()} />
      </div>

      {error && (
        <Banner tone="err" title="문제가 있었습니다" onDismiss={() => setError("")}>{error}</Banner>
      )}

      {/* 회의 준비(prep) 세션은 브리핑 문서 1개만 생성되므로 다른 탭이 없다. */}
      {session.type === "prep" && (
        <p className="mb-2 rounded-card border border-line bg-surface-2 px-3 py-1.5 text-sm text-ink-2">
          회의 준비 브리핑 세션입니다 — <b>회의록</b> 탭에 브리핑이 들어 있습니다
          (스크립트·사실확인 등 다른 문서는 생성되지 않습니다).
        </p>
      )}

      {/* ── 상태별 본문 ─────────────────────────────────────────── */}
      {session.status === "processing" ? (
        <div className="rounded-card border border-line bg-surface p-8 text-center shadow-card">
          {retryNote && (
            <p className="mx-auto mb-4 max-w-md rounded-card border border-line bg-surface-2 px-3 py-2 text-left text-sm text-ink-2">
              {retryNote}
            </p>
          )}
          <h3 className="text-md font-semibold">처리 중입니다</h3>
          <p className="mt-1 text-sm text-ink-3">AI 가 회의 문서를 만들고 있습니다. 이 화면은 자동으로 갱신됩니다.</p>
          {progress && (
            <div className="mx-auto mt-4 max-w-md text-left">
              <div className="mb-1 flex items-baseline justify-between text-sm">
                <span className="font-semibold text-ink-2">{progress.stage || "처리 중"}</span>
                <span className="num text-ink-3">{progress.percent}%</span>
              </div>
              {/* STT 단계는 내부 진행률이 없어 퍼센트가 한동안 멈춘 것처럼 보인다 —
                  움직이는 표시를 겹쳐 '동작 중'임을 알린다. */}
              <ProgressBar percent={progress.percent} label="처리 진행률" indeterminate />
              <p className="mt-1.5 text-xs text-ink-3">
                경과 {formatClock(progress.elapsed)} · 오디오 길이·서버 상황에 따라 수 분 걸릴 수 있습니다.
                {/^(STT|음성 인식)/.test(progress.stage)
                  ? " (음성 인식은 파일 길이에 비례해 가장 오래 걸리며, 이 구간에서는 퍼센트가 잠시 멈춘 것처럼 보일 수 있습니다.)"
                  : ""}
              </p>
            </div>
          )}
          <Button variant="secondary" size="sm" className="mt-5" busy={cancelling} onClick={handleCancel}>
            처리 취소
          </Button>
        </div>
      ) : session.status === "error" ? (
        <div className="rounded-card border border-line bg-surface p-6 shadow-card">
          <h3 className="text-md font-semibold text-rec">처리하지 못했습니다</h3>
          {session.error_detail && (
            <p className="mt-1.5 rounded-card border border-rec bg-rec-bg px-3 py-2 text-sm text-rec">
              {session.error_detail}
            </p>
          )}
          <Button variant="primary" size="sm" className="mt-3" icon={RefreshCw}
            busy={retrying} onClick={handleRetry}>다시 시도</Button>
          <p className="mt-1.5 text-xs text-ink-3">
            음성 인식(STT)이 끝난 뒤 실패한 경우, 재시도는 완료된 전사를 재사용해 비용을
            다시 청구하지 않습니다.
          </p>
        </div>
      ) : tabs.length === 0 ? (
        <EmptyState icon={FileText} title="이 회의에는 아직 문서가 없습니다"
          description="전사만 저장되었거나 생성이 아직 끝나지 않았을 수 있습니다." />
      ) : (
        <div className="flex min-h-0 flex-1 gap-3">
          <div className="flex min-w-0 flex-1 flex-col">
            <Tabs id="doc" items={tabs} value={activeTab} onChange={setActiveTab}
              label="문서 종류" variant="pill" className="mb-2" />

            {/* 문서 툴바 — 본문 위 한 줄. 재생성은 여기 버튼이고, 입력 줄은 눌렀을 때만. */}
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              {REGENERABLE.includes(activeTab) && (
                <Button size="sm" variant="ghost" icon={RefreshCw}
                  onClick={() => setRegenOpen((v) => !v)}>노트 반영해 재생성</Button>
              )}
              <div className="flex-1" />
              <Button size="sm" variant="ghost" icon={copied ? Check : Copy} onClick={handleCopy}>
                {copied ? "복사됨" : "복사"}
              </Button>
              <Button size="sm" variant="ghost" icon={Download} onClick={handleDownload}>다운로드</Button>
              <Button size="sm" variant="ghost" icon={ShareIcon} onClick={handleShare}>공유</Button>
            </div>

            {regenOpen && REGENERABLE.includes(activeTab) && (
              <div className="mb-1.5 flex gap-1.5">
                <Input value={userNotes} onChange={(e) => setUserNotes(e.target.value)}
                  aria-label="재생성 지시" className="min-w-0 flex-1"
                  placeholder="예: 액션 아이템을 표로 정리하고 Q&A 를 더 자세히" />
                <Button variant="primary" size="sm" busy={regenerating}
                  disabled={!userNotes.trim()} onClick={handleRegenerate}>재생성</Button>
              </div>
            )}

            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain rounded-card border
              border-line bg-surface p-4 shadow-card">
              {activeTab === "graph" && graph ? (
                <GraphTab graph={graph} />
              ) : activeTab === "script" && segments.length > 0 ? (
                <ScriptView segments={segments} />
              ) : activeDoc ? (
                activeDoc.format === "json" ? (
                  <pre className="num overflow-x-auto rounded-card bg-surface-2 p-3 text-sm">
                    {(() => {
                      try { return JSON.stringify(JSON.parse(activeDoc.content), null, 2); }
                      catch { return activeDoc.content; }
                    })()}
                  </pre>
                ) : (
                  <Markdown content={activeDoc.content} onWikiLink={onOpenGraph} />
                )
              ) : (
                <p className="py-10 text-center text-sm text-ink-3">해당 문서가 없습니다.</p>
              )}
            </div>
          </div>

          <Inspector
            tabs={[{ key: "notes" as const, label: "참조된 관련 노트", count: related.length }]}
            value="notes"
            onChange={() => {}}
            label="참조된 관련 노트"
          >
            <RelatedNotesPanel notes={related} cross={relatedCross} onOpenNote={onOpenGraph} />
          </Inspector>
        </div>
      )}
    </div>
  );
}

/** 스크립트 탭 — 시간·화자·원문·번역(다를 때). */
function ScriptView({ segments }: { segments: Segment[] }) {
  return (
    <div className="space-y-2.5">
      {segments.map((seg, i) => (
        <div key={i} className="flex gap-3">
          <span className="num w-12 shrink-0 pt-0.5 text-xs text-ink-3">{formatTime(seg.start_time)}</span>
          <div className="min-w-0 flex-1">
            {seg.speaker && <span className="block text-xs font-semibold text-accent">{seg.speaker}</span>}
            <p className="ko-text text-base text-ink">{seg.text}</p>
            {seg.translated_text && seg.translated_text !== seg.text && (
              <p className="ko-text mt-0.5 border-l-2 border-line pl-2 text-sm text-ink-2">
                {seg.translated_text}
              </p>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

import React, { useState, useEffect, useRef } from "react";
import {
  Search, Trash2, RefreshCw, Undo2, XCircle, FileAudio, Square, FileText,
} from "lucide-react";
import {
  getSessions, deleteSession, clearSessions, getTrash, restoreSession, purgeSession,
  retrySession, cancelUpload,
} from "../../lib/api";
import { formatDate, formatClock, typeColor, typeLabel } from "../../lib/format";
import { fmtUsd } from "../../lib/costEstimate";
import { Button, IconButton } from "../../ui/Button";
import { StatusPill, Tag, statusTone } from "../../ui/StatusPill";
import DataTable, { type Column } from "../../ui/DataTable";
import { Input, Select } from "../../ui/Field";
import { LoadingBlock, EmptyState, ErrorState } from "../../ui/states";
import CostSummaryCard from "./CostSummaryCard";
import type { Session } from "../../lib/types";

/**
 * 라이브러리 — 모든 세션의 허브 (PRD §6.1).
 *
 * 데이터 계약에서 **없는 것을 그리지 않는다.** 프로토타입이 약속한 것 중 서버에 원천이
 * 없는 셋은 이렇게 처리한다:
 *  - '계획' 상태: DB status 값은 processing/completed/error/pending 뿐이다. 계획은 회의 준비
 *    브리핑 세션(`type === "prep"`)으로만 존재하므로 그 행의 동작은 [브리핑 열기]다.
 *  - '화자 수': `Session.speakers` 는 **참석자 명단 문자열**이지 수가 아니다 → 콤마로 세고
 *    비면 `—`.
 *  - '내용 검색': 검색은 클라이언트에서 제목·유형만 본다(lib/api.getSessions). placeholder 에
 *    "내용"이라고 적으면 거짓말이 된다.
 * 상태 필터도 서버 파라미터가 없어 받아 온 목록에서 거른다.
 */

const PAGE = 50;

/** 참석자 명단 문자열 → 사람 수. "홍길동, 김영희" → 2. 비면 표시하지 않는다. */
function speakerCount(speakers?: string): string {
  const n = (speakers || "").split(/[,·]/).map((s) => s.trim()).filter(Boolean).length;
  return n > 0 ? String(n) : "—";
}

/** 기계 생성 세션명(web_realtime_65032efb-…)을 사용자에게 보여주지 않는다(PRD §10). */
function displayTitle(s: Session): string {
  const t = (s.title || "").trim();
  if (!t) return "제목 없음";
  return /^(web_realtime_|web_upload_)[0-9a-f-]{8,}$/i.test(t) ? "제목 없음" : t;
}

interface Props {
  onSelectSession: (id: string) => void;
  onNewUpload: () => void;
  onNewRecord: () => void;
}

export default function Library({ onSelectSession, onNewUpload, onNewRecord }: Props) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [search, setSearch] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [loading, setLoading] = useState(true);
  // 조회 실패를 '세션이 없음'과 구분한다 — 과거엔 console.error 만 하고 빈 상태를
  // 그려서, 백엔드가 죽은 것과 회의가 하나도 없는 것이 화면상 똑같았다.
  const [loadError, setLoadError] = useState("");
  // 휴지통 보기. 삭제가 soft delete 이므로 되돌릴 자리가 화면에 있어야 한다.
  const [showTrash, setShowTrash] = useState(false);
  // 목록은 처음 N건만 그린다 — 전량 .map() 은 사내 1년 누적(수백~수천 건)에서 첫 렌더가
  // 눈에 띄게 느려진다. 가상화 라이브러리는 넣지 않는다(오프라인 번들·의존성 0 정책).
  const [visibleCount, setVisibleCount] = useState(PAGE);
  const [notice, setNotice] = useState<{ text: string; undoId?: string } | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  // 응답이 순서 뒤바뀌어 도착해도 마지막 요청의 결과만 반영한다.
  const reqSeqRef = useRef(0);

  const load = async (background = false) => {
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

  // 검색어는 디바운스한다 — 한글은 자모마다 change 가 떠서 "회의록" 입력에도 요청이
  // 7회 이상 나갔다. 유형 필터는 클릭이라 즉시 반영한다.
  useEffect(() => {
    setVisibleCount(PAGE);
    const t = setTimeout(() => { load(); }, search ? 300 : 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, typeFilter, showTrash]);

  // 처리 중인 세션 폴링 — setState updater 안에서 load()를 호출하면 StrictMode에서
  // 이중 실행되는 부수효과가 생기므로 ref로 현재 목록을 읽는다.
  const sessionsRef = useRef<Session[]>([]);
  useEffect(() => { sessionsRef.current = sessions; }, [sessions]);
  useEffect(() => {
    const t = setInterval(() => {
      if (sessionsRef.current.some((s) => s.status === "processing")) load(true);
    }, 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search, typeFilter]);

  const handleDelete = async (s: Session) => {
    if (!confirm("이 회의를 휴지통으로 보낼까요? 나중에 되돌릴 수 있습니다.")) return;
    const r = await deleteSession(s.id);
    // 되돌릴 수 있다는 사실을 알려야 휴지통이 의미가 있다(예전에는 회복 불가였다).
    setNotice(r.restorable
      ? { text: "휴지통으로 보냈습니다.", undoId: s.id }
      : { text: "삭제했습니다." });
    load();
  };

  const handleClearAll = async () => {
    if (!confirm("모든 회의 기록을 휴지통으로 보낼까요? 나중에 되돌릴 수 있습니다.")) return;
    await clearSessions();
    setNotice({ text: "모두 휴지통으로 보냈습니다. [휴지통]에서 되돌릴 수 있습니다." });
    load();
  };

  const handleRestore = async (id: string) => {
    try {
      await restoreSession(id);
      setNotice({ text: "되돌렸습니다." });
      load();
    } catch (err) {
      setNotice({ text: err instanceof Error ? err.message : String(err) });
    }
  };

  const handlePurge = async (s: Session) => {
    if (!confirm("완전히 삭제할까요? 회의록·전사 폴더는 Windows 휴지통으로 보냅니다.")) return;
    try {
      const r = await purgeSession(s.id);
      // 폴더가 실제로 어떻게 됐는지는 서버만 안다 — 화면이 자기 문구를 만들면 거짓이 될 수 있다.
      setNotice({ text: r.message || "완전히 삭제했습니다." });
      load();
    } catch (err) {
      setNotice({ text: err instanceof Error ? err.message : String(err) });
    }
  };

  const handleRetry = async (s: Session) => {
    setBusyId(s.id);
    try {
      const r = await retrySession(s.id);
      setNotice({
        text: r.reusedStt === false
          ? "음성 인식부터 다시 처리합니다 — API 비용이 다시 발생할 수 있습니다."
          : "완료된 전사를 재사용해 이어서 처리합니다 — 음성 인식 비용은 다시 청구되지 않습니다.",
      });
      load();
    } catch (err) {
      setNotice({ text: err instanceof Error ? err.message : "재시도에 실패했습니다." });
    } finally {
      setBusyId(null);
    }
  };

  const handleCancel = async (s: Session) => {
    if (!confirm("처리를 취소하시겠습니까? 현재 단계가 끝나면 중단되고 이 회의는 삭제됩니다.")) return;
    setBusyId(s.id);
    const r = await cancelUpload(s.id);
    setBusyId(null);
    setNotice({ text: r.message || (r.ok ? "취소를 요청했습니다." : "취소할 수 없습니다.") });
    load();
  };

  // 상태 필터는 서버 파라미터가 없어 여기서 거른다(GET /api/sessions 는 search·type 만 받는다).
  const filtered = statusFilter ? sessions.filter((s) => s.status === statusFilter) : sessions;
  const rows = filtered.slice(0, visibleCount);

  const columns: Column<Session>[] = [
    {
      key: "title", header: "제목", width: "32%",
      cell: (s) => (
        <div className="min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="truncate font-semibold text-ink">{displayTitle(s)}</span>
            {s.source === "cli" && <Tag>CLI</Tag>}
          </div>
          {(s.topic || s.error_detail) && (
            <p className="truncate text-xs text-ink-3" title={s.error_detail || s.topic}>
              {s.status === "error" && s.error_detail ? s.error_detail : s.topic}
            </p>
          )}
        </div>
      ),
    },
    { key: "type", header: "유형", cell: (s) => <Tag tone={typeColor(s.type)}>{typeLabel(s.type)}</Tag> },
    {
      key: "status", header: "상태",
      cell: (s) => (
        <span className="inline-flex items-center gap-1">
          <StatusPill tone={statusTone(s.status)} pulse={s.status === "processing"}>
            {s.status === "error" ? "오류" : statusText(s)}
          </StatusPill>
          {!!s.translate && <Tag title="원문과 번역이 함께 저장된 회의">번역됨</Tag>}
        </span>
      ),
    },
    { key: "speakers", header: "화자", cardHidden: true, cell: (s) => <span className="num text-ink-3">{speakerCount(s.speakers)}</span> },
    {
      key: "duration", header: "길이", align: "right",
      cell: (s) => <span className="num text-ink-2">{s.duration_sec > 0 ? formatClock(s.duration_sec) : "—"}</span>,
    },
    {
      key: "cost", header: "비용", align: "right",
      cell: (s) => <span className="num text-ink-2">{s.cost_estimate > 0 ? fmtUsd(s.cost_estimate, 2) : "—"}</span>,
    },
    {
      key: "date", header: "날짜", align: "right",
      cell: (s) => <span className="text-ink-3">{formatDate(s.date || s.created_at)}</span>,
    },
  ];

  const actions = (s: Session) => {
    if (showTrash) {
      return (
        <>
          <IconButton icon={Undo2} size="sm" label={`${displayTitle(s)} 되돌리기`}
            onClick={(e) => { e.stopPropagation(); handleRestore(s.id); }} />
          <IconButton icon={XCircle} size="sm" label={`${displayTitle(s)} 완전 삭제`}
            onClick={(e) => { e.stopPropagation(); handlePurge(s); }} />
        </>
      );
    }
    return (
      <>
        {s.status === "error" && (
          <Button size="sm" variant="secondary" icon={RefreshCw} busy={busyId === s.id}
            onClick={(e) => { e.stopPropagation(); handleRetry(s); }}>재시도</Button>
        )}
        {s.status === "processing" && (
          <IconButton icon={Square} size="sm" label={`${displayTitle(s)} 처리 취소`}
            busy={busyId === s.id}
            onClick={(e) => { e.stopPropagation(); handleCancel(s); }} />
        )}
        {s.type === "prep" && (
          <IconButton icon={FileText} size="sm" label={`${displayTitle(s)} 브리핑 열기`}
            onClick={(e) => { e.stopPropagation(); onSelectSession(s.id); }} />
        )}
        <IconButton icon={Trash2} size="sm" label={`${displayTitle(s)} 휴지통으로 보내기`}
          onClick={(e) => { e.stopPropagation(); handleDelete(s); }} />
      </>
    );
  };

  return (
    <div className="mx-auto w-full max-w-6xl">
      {!showTrash && <CostSummaryCard sessions={sessions} />}

      <div className="mb-2 flex flex-wrap items-center gap-2">
        <div className="relative min-w-[200px] flex-1 sm:max-w-xs">
          <Search size={14} aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
          <Input aria-label="회의 검색" value={search} onChange={(e) => setSearch(e.target.value)}
            placeholder="제목·유형 검색" className="pl-8" />
        </div>
        <Select aria-label="유형 필터" value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}
          className="w-auto">
          <option value="">전체 유형</option>
          <option value="meeting">회의</option>
          <option value="seminar">세미나</option>
          <option value="lecture">강의</option>
          <option value="prep">회의 준비</option>
        </Select>
        <Select aria-label="상태 필터" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
          className="w-auto">
          <option value="">전체 상태</option>
          <option value="completed">완료</option>
          <option value="processing">처리 중</option>
          <option value="error">오류</option>
          <option value="pending">대기 중</option>
        </Select>
        <IconButton icon={RefreshCw} label="목록 새로고침" onClick={() => load()} variant="secondary" />
        <div className="flex-1" />
        <Button size="sm" variant={showTrash ? "primary" : "secondary"} icon={Trash2}
          onClick={() => { setNotice(null); setShowTrash((v) => !v); }}>
          {showTrash ? "목록으로" : "휴지통"}
        </Button>
        {!showTrash && sessions.length > 0 && (
          <Button size="sm" variant="secondary" onClick={handleClearAll}>전체 삭제</Button>
        )}
      </div>

      {/* 삭제/복구 결과 알림 — 되돌릴 수 있다는 사실을 여기서 알린다. */}
      {notice && (
        <div className="mb-2 flex items-center justify-between gap-3 rounded-card border border-line
          bg-surface-2 px-3 py-2 text-sm">
          <span className="text-ink">{notice.text}</span>
          <span className="flex shrink-0 items-center gap-1">
            {notice.undoId && (
              <Button size="sm" variant="secondary" icon={Undo2}
                onClick={() => { const id = notice.undoId!; setNotice(null); handleRestore(id); }}>
                되돌리기
              </Button>
            )}
            <IconButton icon={XCircle} size="sm" label="알림 닫기" onClick={() => setNotice(null)} />
          </span>
        </div>
      )}
      {showTrash && (
        <p className="mb-2 text-xs text-ink-3">
          휴지통의 회의는 목록에 보이지 않지만 그대로 남아 있습니다. [완전 삭제]를 누르면
          회의록·전사 폴더를 Windows 휴지통으로 보냅니다.
        </p>
      )}

      {/* 건수는 목록 바로 위에 둔다 — 필터를 걸었을 때 "몇 건이 걸렸나"가 표 밖에 있어야
          한다. 상단 요약 카드에도 총계가 있지만 그쪽은 필터와 무관한 이번 달 수치다. */}
      {!loading && !loadError && (
        <p className="mb-1.5 text-xs text-ink-3">
          {showTrash ? `휴지통 ${filtered.length}건` : `회의 ${filtered.length}건`}
          {!showTrash && filtered.length !== sessions.length && ` (전체 ${sessions.length}건)`}
        </p>
      )}

      {loading ? (
        <LoadingBlock label="회의 목록을 불러오는 중" />
      ) : loadError ? (
        <ErrorState
          title="회의 목록을 불러올 수 없습니다"
          detail={`서버가 실행 중인지 확인해 주세요. (회의가 없는 것이 아니라 조회에 실패했습니다) ${loadError}`}
          onRetry={() => load()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={FileAudio}
          title={showTrash ? "휴지통이 비어 있습니다" : sessions.length ? "조건에 맞는 회의가 없습니다" : "아직 회의가 없습니다"}
          description={showTrash
            ? "삭제한 회의가 여기 모입니다."
            : sessions.length ? "검색어나 필터를 바꿔 보세요." : "녹음을 시작하거나 파일을 올려 보세요."}
          action={!showTrash && !sessions.length && (
            <span className="flex gap-2">
              <Button variant="primary" onClick={onNewRecord}>녹음 시작</Button>
              <Button variant="secondary" onClick={onNewUpload}>파일 업로드</Button>
            </span>
          )}
        />
      ) : (
        <>
          <DataTable
            rows={rows}
            columns={columns}
            rowKey={(s) => s.id}
            caption={showTrash ? "휴지통의 회의 목록" : "회의 목록"}
            onRowClick={(s) => onSelectSession(s.id)}
            rowLabel={(s) => `${displayTitle(s)} 열기`}
            rowTone={(s) => (s.status === "processing" ? "proc" : s.status === "error" ? "err" : undefined)}
            actions={actions}
          />
          {/* 잘라낸 만큼을 숨기지 않고 알린다 — 조용한 절단은 "다 보여줬다"로 읽힌다 */}
          {filtered.length > visibleCount && (
            <Button variant="secondary" className="mt-2 w-full"
              onClick={() => setVisibleCount((n) => n + PAGE)}>
              더 보기 ({filtered.length - visibleCount}개 남음)
            </Button>
          )}
        </>
      )}
    </div>
  );
}

/** 상태 배지 문구 — '계획'은 서버 상태가 아니라 준비 브리핑 세션을 뜻한다(FR-LIB-2). */
function statusText(s: Session): string {
  if (s.type === "prep" && s.status === "completed") return "계획";
  return ({ completed: "완료", processing: "처리 중", pending: "대기 중" } as Record<string, string>)[s.status]
    || s.status;
}

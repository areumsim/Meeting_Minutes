import React, { useEffect, useState } from "react";
import {
  CalendarClock, GitMerge, FileAudio, RefreshCw, Play, Square, Zap,
} from "lucide-react";
import {
  assistantStatus, assistantSchedule, getMerges, doMerge, vaultAudio, vaultAudioStatus,
  planStatus, planStart, planStop, getCostSummary,
  type AssistantSummary, type PendingMerge, type PlanAutoStatus,
} from "../../lib/api";
import { Button } from "../../ui/Button";
import { StatusPill, Tag } from "../../ui/StatusPill";
import CostConfirmModal from "../../ui/CostConfirmModal";

/**
 * 회의 비서 4카드 — PRD FR-AST-1·FR-AST-2.
 *
 * 과금이 시작되는 둘(노트 첨부 오디오 처리 · 계획 자동화 시작)은 **확인을 거친다.**
 * 다만 그 두 엔드포인트는 예상 금액을 주지 않는다(watcher.py 는 spend_guard 로 거절만 한다)
 * → 금액 없는 확인 변형을 쓴다. 프런트가 STT 단가를 다시 계산하지 않는다.
 *
 * 미리보기(dry-run)와 새로고침은 과금이 없어 확인 없이 바로 돈다 — 모든 버튼에 확인을 걸면
 * 확인이 배경음이 되어 정작 돈 쓰는 버튼에서도 그냥 누르게 된다.
 */

function Card({
  title, icon, desc, children,
}: {
  title: string; icon: React.ReactNode; desc?: string; children: React.ReactNode;
}) {
  return (
    <section className="rounded-card border border-line bg-surface p-3 shadow-card">
      <h3 className="flex items-center gap-1.5 text-md font-semibold text-ink">{icon} {title}</h3>
      {desc && <p className="mt-0.5 text-xs text-ink-3">{desc}</p>}
      <div className="mt-2.5">{children}</div>
    </section>
  );
}

/** 카드마다 반복되던 "마지막 실행 결과 한 줄" — 서버 메시지를 그대로 보여준다. */
function Result({ text }: { text: string }) {
  return text ? <p className="mt-2 text-xs text-ink-3">{text}</p> : null;
}

/** 이번 달 지출·한도 — 금액 없는 확인 모달에 넣을 서버 값. */
function useMonthlySpend() {
  const [spend, setSpend] = useState<{ mtd: number; cap: number } | null>(null);
  useEffect(() => {
    getCostSummary().then((s) => s && setSpend({ mtd: s.monthToDateUsd, cap: s.monthlyCapUsd }));
  }, []);
  return spend;
}

export default function AssistantPanel() {
  return (
    <div className="mx-auto grid max-w-5xl gap-3 lg:grid-cols-2">
      <StatusCard />
      <MergeCard />
      <VaultAudioCard />
      <PlanAutoCard />
    </div>
  );
}

function StatusCard() {
  const [data, setData] = useState<AssistantSummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = async () => { setBusy(true); setData(await assistantStatus(7)); setBusy(false); };
  useEffect(() => { load(); }, []);

  const writeDash = async () => {
    setBusy(true); setMsg("");
    const r = await assistantSchedule(14, true);
    setData(r);
    setMsg(r.ok ? (r.dashboard_path ? `일정 대시보드 갱신 → ${r.dashboard_path}` : "갱신했습니다.")
                : (r.message || "갱신하지 못했습니다."));
    setBusy(false);
  };

  const c = data?.counts;
  return (
    <Card title="일정 · 현황" icon={<CalendarClock size={15} />}
      desc="다가오는 회의·충돌·준비미비·병합대기를 노트 폴더에서 모읍니다.">
      {data && !data.ok && <p className="mb-2 text-sm text-warn">{data.message}</p>}
      {c && (
        <div className="mb-2 flex flex-wrap gap-1.5">
          <Tag>회의 {c.meetings}</Tag>
          {c.conflicts > 0
            ? <StatusPill tone="err">충돌 {c.conflicts}</StatusPill>
            : <Tag>충돌 0</Tag>}
          {c.warnings > 0
            ? <StatusPill tone="warn">준비미비 {c.warnings}</StatusPill>
            : <Tag>준비미비 0</Tag>}
          <Tag>병합대기 {c.pending_merges}</Tag>
        </div>
      )}
      {data?.summary && (
        <pre className="ko-text mb-2 max-h-60 overflow-auto whitespace-pre-wrap rounded-card
          border border-line bg-surface-2 p-2 text-xs text-ink-2">{data.summary}</pre>
      )}
      <div className="flex flex-wrap gap-1.5">
        <Button size="sm" variant="secondary" icon={RefreshCw} busy={busy} onClick={load}>새로고침</Button>
        <Button size="sm" variant="primary" icon={CalendarClock} busy={busy} onClick={writeDash}>
          일정 대시보드 갱신
        </Button>
      </div>
      <Result text={msg} />
    </Card>
  );
}

function MergeCard() {
  const [pending, setPending] = useState<PendingMerge[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const load = async () => {
    setBusy(true); setMsg("");
    const r = await getMerges();
    if (r.ok) setPending(r.pending || []);
    else setMsg(r.message || "목록을 가져오지 못했습니다.");
    setBusy(false);
  };
  useEffect(() => { load(); }, []);

  const merge = async (p: PendingMerge) => {
    if (!confirm(`녹음 '${p.recording_title}' 을(를) 계획 '${p.plan_title}' 에 병합할까요?`)) return;
    setBusy(true); setMsg("");
    const r = await doMerge(p.recording_path, false);
    setMsg(r.message);
    await load();
    setBusy(false);
  };

  return (
    <Card title="녹음 ↔ 계획 병합" icon={<GitMerge size={15} />}
      desc="계획 노트와 매칭된 녹음을 확인한 뒤 병합합니다(Obsidian REST 연결 필요).">
      {pending.length === 0 ? (
        <p className="text-sm text-ink-3">병합 대기 항목이 없습니다.</p>
      ) : (
        <ul className="mb-2 space-y-1.5">
          {pending.map((p) => (
            <li key={p.recording_path}
              className="flex items-center justify-between gap-2 rounded-ctl border border-line
                bg-surface-2 px-2 py-1.5 text-sm">
              <span className="min-w-0 truncate">
                <b>{p.recording_title}</b> → {p.plan_title}
              </span>
              <Button size="sm" variant="secondary" icon={GitMerge} busy={busy}
                onClick={() => merge(p)}>병합</Button>
            </li>
          ))}
        </ul>
      )}
      <Button size="sm" variant="secondary" icon={RefreshCw} busy={busy} onClick={load}>새로고침</Button>
      <Result text={msg} />
    </Card>
  );
}

function VaultAudioCard() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [running, setRunning] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [preview, setPreview] = useState<string>("");
  const spend = useMonthlySpend();

  useEffect(() => {
    const id = setInterval(async () => {
      const s = await vaultAudioStatus();
      setRunning(s.running);
      if (s.message) setMsg(s.message);
    }, 4000);
    return () => clearInterval(id);
  }, []);

  // 미리보기는 dry-run 이라 과금이 없다 — 확인 없이 돈다. 그 결과(대상 건수)를 확인
  // 모달의 '규모'로 재활용한다: 서버가 금액을 주지 않으니 이것이 유일한 정량 정보다.
  const runPreview = async () => {
    setBusy(true);
    const r = await vaultAudio(true);
    setMsg(r.message);
    setPreview(r.count != null ? `${r.count}건` : r.message);
    setBusy(false);
  };

  const start = async () => {
    setBusy(true);
    const r = await vaultAudio(false);
    setMsg(r.message);
    setRunning(!!r.running);
    setBusy(false);
    setConfirming(false);
  };

  return (
    <Card title="노트 첨부 오디오" icon={<FileAudio size={15} />}
      desc="노트에 첨부·임베드된 녹음을 찾아 회의록으로 정리합니다.">
      {confirming && (
        <CostConfirmModal
          title="처리를 시작할까요?"
          what="찾은 녹음을 전사하고 회의록을 만듭니다 — 파일 길이에 비례해 API 비용이 듭니다."
          targets={preview ? [{ label: "처리 대상", value: preview }] : undefined}
          monthToDateUsd={spend?.mtd}
          monthlyCapUsd={spend?.cap}
          confirmLabel="처리 실행"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={start}
        />
      )}
      <div className="flex flex-wrap items-center gap-1.5">
        <Button size="sm" variant="secondary" icon={FileAudio} busy={busy} onClick={runPreview}>
          미리보기(대상 집계)
        </Button>
        <Button size="sm" variant="primary" icon={Play} disabled={running} busy={busy && !confirming}
          onClick={() => setConfirming(true)}>
          {running ? "처리 중…" : "처리 실행"}
        </Button>
      </div>
      <Result text={msg} />
    </Card>
  );
}

function PlanAutoCard() {
  const [st, setSt] = useState<PlanAutoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [confirming, setConfirming] = useState(false);
  const spend = useMonthlySpend();

  const refresh = async () => setSt(await planStatus());
  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, []);

  const start = async () => {
    setBusy(true);
    const r = await planStart();
    setMsg(r.message);
    await refresh();
    setBusy(false);
    setConfirming(false);
  };
  const stop = async () => {
    setBusy(true);
    const r = await planStop();
    setMsg(r.message);
    await refresh();
    setBusy(false);
  };

  const running = !!st?.running;
  return (
    <Card title="계획 자동화" icon={<Zap size={15} />}
      desc="켜 두면 계획된 회의 노트에 사전 리서치를 자동으로 쓰고, 새 첨부 녹음을 처리합니다.">
      {confirming && (
        <CostConfirmModal
          title="자동화를 시작할까요?"
          what="켜 두는 동안 사전 리서치(LLM)와 첨부 녹음 처리(STT)가 사람 확인 없이 실행됩니다."
          monthToDateUsd={spend?.mtd}
          monthlyCapUsd={spend?.cap}
          confirmLabel="자동화 시작"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={start}
        />
      )}
      <div className="mb-2 flex flex-wrap gap-1.5">
        {running
          ? <StatusPill tone="ok" pulse>실행 중</StatusPill>
          : <StatusPill tone="idle">중지됨</StatusPill>}
        {st && <Tag>리서치 {st.notes_researched}</Tag>}
        {st && <Tag>오디오 {st.audio_processed}</Tag>}
      </div>
      {st?.error && <p role="alert" className="mb-2 text-xs text-rec">{st.error}</p>}
      {running
        ? <Button size="sm" variant="secondary" icon={Square} busy={busy} onClick={stop}>자동화 중지</Button>
        : <Button size="sm" variant="primary" icon={Play} busy={busy && !confirming}
            onClick={() => setConfirming(true)}>자동화 시작</Button>}
      <Result text={msg} />
    </Card>
  );
}

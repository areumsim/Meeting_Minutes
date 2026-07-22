import { useState, useEffect } from "react";
import { Loader2, RefreshCw, CalendarClock, GitMerge, FileAudio, Play, Square, CheckCircle, XCircle } from "lucide-react";
import {
  assistantStatus, assistantSchedule, getMerges, doMerge,
  vaultAudio, vaultAudioStatus, planStatus, planStart, planStop,
  type AssistantSummary, type PendingMerge, type PlanAutoStatus,
} from "../lib/api";

export default function Assistant() {
  return (
    <div className="max-w-3xl mx-auto px-1 md:px-0 space-y-3">
      <h2 className="text-2xl font-bold tracking-tight mb-1 flex items-center gap-2">
        <CalendarClock size={22} /> 회의 비서
      </h2>
      <p className="text-sm text-brand-500 mb-2">
        Obsidian 볼트의 회의 일정·계획을 정리하고, 노트에 첨부된 녹음을 자동 처리합니다.
        (볼트 폴더가 [설정]에 지정돼 있어야 합니다)
      </p>
      <StatusCard />
      <MergeCard />
      <VaultAudioCard />
      <PlanAutoCard />
    </div>
  );
}

function Card({ title, icon, desc, children }: { title: string; icon: React.ReactNode; desc?: string; children: React.ReactNode }) {
  return (
    <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 shadow-sm">
      <h3 className="text-base font-bold mb-1 flex items-center gap-2 text-brand-900">{icon} {title}</h3>
      {desc && <p className="text-xs text-brand-500 mb-3">{desc}</p>}
      {children}
    </section>
  );
}

function Btn({ onClick, busy, children, variant = "primary" }: { onClick: () => void; busy?: boolean; children: React.ReactNode; variant?: "primary" | "ghost" | "dark" }) {
  const cls = variant === "primary"
    ? "bg-brand-950 text-white hover:bg-brand-900"
    : variant === "dark" ? "bg-zinc-800 text-white hover:bg-zinc-900"
    : "bg-brand-50 text-brand-700 hover:bg-brand-100";
  return (
    <button onClick={onClick} disabled={busy}
      className={`flex items-center gap-2 px-5 py-2.5 rounded-xl text-sm font-bold transition-all ${cls}`}>
      {busy ? <Loader2 size={16} className="animate-spin" /> : null}{children}
    </button>
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
    setMsg(r.ok ? (r.dashboard_path ? `대시보드 갱신 → ${r.dashboard_path}` : "갱신됨") : (r.message || "실패"));
    setBusy(false);
  };

  const c = data?.counts;
  return (
    <Card title="일정·현황" icon={<CalendarClock size={16} />} desc="다가오는 회의·충돌·준비미비·병합대기 요약.">
      {data && !data.ok && <div className="text-sm text-amber-600 mb-2">{data.message}</div>}
      {c && (
        <div className="flex flex-wrap gap-2 text-xs mb-3">
          <span className="px-2.5 py-1 rounded-lg font-bold bg-brand-50 text-brand-700">회의 {c.meetings}</span>
          <span className="px-2.5 py-1 rounded-lg font-bold bg-red-50 text-red-700">충돌 {c.conflicts}</span>
          <span className="px-2.5 py-1 rounded-lg font-bold bg-amber-50 text-amber-700">준비미비 {c.warnings}</span>
          <span className="px-2.5 py-1 rounded-lg font-bold bg-sky-50 text-sky-700">병합대기 {c.pending_merges}</span>
        </div>
      )}
      {data?.summary && (
        <pre className="text-xs bg-zinc-50 border border-zinc-200 rounded-lg p-3 whitespace-pre-wrap max-h-72 overflow-auto mb-3">{data.summary}</pre>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Btn onClick={load} busy={busy} variant="ghost"><RefreshCw size={16} /> 새로고침</Btn>
        <Btn onClick={writeDash} busy={busy}><CalendarClock size={16} /> 일정 대시보드 갱신</Btn>
      </div>
      {msg && <p className="text-xs text-brand-500 mt-2">{msg}</p>}
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
    if (r.ok) setPending(r.pending || []); else setMsg(r.message || "실패");
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
    <Card title="녹음↔계획 병합" icon={<GitMerge size={16} />} desc="계획과 매칭된 녹음을 확인 후 계획 노트에 병합(Obsidian REST 필요).">
      {pending.length === 0 ? (
        <p className="text-sm text-brand-400">병합 대기 항목이 없습니다.</p>
      ) : (
        <div className="space-y-2 mb-3">
          {pending.map((p) => (
            <div key={p.recording_path} className="flex items-center justify-between gap-3 text-sm bg-zinc-50 border border-zinc-200 rounded-lg px-3 py-2">
              <span className="truncate">🎙 <b>{p.recording_title}</b> → 📋 {p.plan_title}</span>
              <Btn onClick={() => merge(p)} busy={busy} variant="ghost"><GitMerge size={15} /> 병합</Btn>
            </div>
          ))}
        </div>
      )}
      <Btn onClick={load} busy={busy} variant="ghost"><RefreshCw size={16} /> 새로고침</Btn>
      {msg && <p className="text-xs text-brand-500 mt-2">{msg}</p>}
    </Card>
  );
}

function VaultAudioCard() {
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");
  const [running, setRunning] = useState(false);

  useEffect(() => {
    const id = setInterval(async () => {
      const s = await vaultAudioStatus();
      setRunning(s.running);
      if (s.message) setMsg(s.message);
    }, 4000);
    return () => clearInterval(id);
  }, []);

  const preview = async () => { setBusy(true); const r = await vaultAudio(true); setMsg(r.message); setBusy(false); };
  const run = async () => { setBusy(true); const r = await vaultAudio(false); setMsg(r.message); setRunning(!!r.running); setBusy(false); };

  return (
    <Card title="노트 첨부 오디오 처리" icon={<FileAudio size={16} />} desc="Obsidian 노트에 첨부/임베드된 녹음을 찾아 회의록으로 정리·병합합니다.">
      <div className="flex flex-wrap items-center gap-3">
        <Btn onClick={preview} busy={busy} variant="ghost"><FileAudio size={16} /> 미리보기(대상 집계)</Btn>
        <Btn onClick={run} busy={busy || running}><Play size={16} /> {running ? "처리 중..." : "처리 실행"}</Btn>
      </div>
      {msg && <p className="text-xs text-brand-500 mt-2">{msg}</p>}
    </Card>
  );
}

function PlanAutoCard() {
  const [st, setSt] = useState<PlanAutoStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  const refresh = async () => setSt(await planStatus());
  useEffect(() => { refresh(); const id = setInterval(refresh, 5000); return () => clearInterval(id); }, []);

  const start = async () => { setBusy(true); const r = await planStart(); setMsg(r.message); await refresh(); setBusy(false); };
  const stop = async () => { setBusy(true); const r = await planStop(); setMsg(r.message); await refresh(); setBusy(false); };
  const running = !!st?.running;

  return (
    <Card title="계획 자동화" icon={running ? <CheckCircle size={16} className="text-emerald-600" /> : <XCircle size={16} className="text-zinc-400" />}
      desc="켜 두면 planned 회의 노트에 사전 리서치를 자동 작성하고, 새로 첨부된 녹음을 자동 처리합니다.">
      <div className="flex flex-wrap gap-2 text-xs mb-3">
        <span className={`px-2.5 py-1 rounded-lg font-bold ${running ? "bg-emerald-100 text-emerald-700" : "bg-zinc-100 text-zinc-500"}`}>
          {running ? "● 실행 중" : "○ 중지됨"}
        </span>
        {st && <span className="px-2.5 py-1 rounded-lg font-bold bg-brand-50 text-brand-700">리서치 {st.notes_researched}</span>}
        {st && <span className="px-2.5 py-1 rounded-lg font-bold bg-sky-50 text-sky-700">오디오 {st.audio_processed}</span>}
      </div>
      {st?.error && <div className="text-xs text-red-600 mb-2">{st.error}</div>}
      <div className="flex items-center gap-3">
        {running
          ? <Btn onClick={stop} busy={busy} variant="dark"><Square size={16} /> 자동화 중지</Btn>
          : <Btn onClick={start} busy={busy}><Play size={16} /> 자동화 시작</Btn>}
      </div>
      {msg && <p className="text-xs text-brand-500 mt-2">{msg}</p>}
    </Card>
  );
}

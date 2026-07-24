import React, { useState } from "react";
import { ClipboardList, Loader2, Copy, CheckCircle, Save, FileText, Link2, AlertTriangle } from "lucide-react";
import Markdown from "./Markdown";
import { prepBrief, savePrepBrief, type PrepBriefResult } from "../lib/api";

export default function PrepBrief({ onSaved }: { onSaved?: (id: string) => void }) {
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [date, setDate] = useState("");
  const [attendees, setAttendees] = useState("");
  const [notes, setNotes] = useState("");
  const [meta, setMeta] = useState<PrepBriefResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [brief, setBrief] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedId, setSavedId] = useState<string | null>(null);

  const run = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setError("");
    setBrief("");
    setSavedId(null);
    setMeta(null);
    const res = await prepBrief(title.trim(), topic.trim(), { attendees: attendees.trim(), notes: notes.trim() });
    if (res.ok && res.brief) { setBrief(res.brief); setMeta(res); }
    else setError(res.message || "브리핑을 생성하지 못했습니다.");
    setLoading(false);
  };

  const copy = () => {
    navigator.clipboard?.writeText(brief);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const save = async () => {
    if (!brief) return;
    setSaving(true);
    setError("");
    const res = await savePrepBrief({
      title: title.trim(), brief, topic: topic.trim(), date: date.trim(), attendees: attendees.trim(),
    });
    if (res.ok && res.sessionId) setSavedId(res.sessionId);
    else setError(res.message || "저장에 실패했습니다.");
    setSaving(false);
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">회의 준비 브리핑</h2>
      <p className="text-brand-500 mb-4 text-sm">제목/주제로 볼트의 관련 노트·이전 결정·미완료 액션을 모아 준비 자료를 만듭니다. 저장하면 대시보드에서 다시 볼 수 있습니다.</p>

      <div className="bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5 space-y-3">
        <div className="space-y-1.5">
          <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">회의 제목</label>
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") run(); }}
            placeholder="예: 3분기 로드맵 검토"
            className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-brand-500 outline-none text-sm font-medium"
          />
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">회의 날짜 (선택)</label>
            <input
              type="date"
              value={date}
              onChange={(e) => setDate(e.target.value)}
              className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-brand-500 outline-none text-sm font-medium"
            />
          </div>
          <div className="space-y-1.5">
            <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">참석자 (선택)</label>
            <input
              type="text"
              value={attendees}
              onChange={(e) => setAttendees(e.target.value)}
              placeholder="예: 홍길동, 김영희"
              className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-brand-500 outline-none text-sm font-medium"
            />
          </div>
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">주제 / 키워드 (선택)</label>
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") run(); }}
            placeholder="예: 로드맵, 우선순위, 예산"
            className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-brand-500 outline-none text-sm font-medium"
          />
        </div>
        <div className="space-y-1.5">
          <label className="text-xs font-bold text-zinc-400 uppercase tracking-widest">추가 노트 / 맥락 (선택)</label>
          <textarea
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
            placeholder="배경 정보, 확인할 안건, 관련 키워드 등을 자유롭게 적으면 관련 노트 검색·브리핑에 반영됩니다."
            className="w-full px-3 py-2.5 bg-zinc-50 border border-zinc-200 rounded-lg focus:ring-2 focus:ring-brand-500 outline-none text-sm min-h-[72px] resize-y"
          />
        </div>
        <button
          onClick={run}
          disabled={loading || !title.trim()}
          className="w-full flex items-center justify-center gap-2 py-3 bg-brand-950 text-white rounded-xl font-bold hover:bg-brand-900 transition-all shadow-lg disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
        >
          {loading ? <Loader2 className="animate-spin" size={18} /> : <ClipboardList size={18} />}
          {loading ? "생성 중..." : "브리핑 생성"}
        </button>
      </div>

      {error && (
        <div className="mt-4 rounded-xl border border-red-200 bg-red-50 text-red-600 px-4 py-3 text-sm">{error}</div>
      )}

      {meta && (
        <div className="mt-4 bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5">
          <div className="flex items-center gap-2 mb-2">
            <Link2 size={16} className="text-brand-500" />
            <span className="text-sm font-bold text-brand-900">연결된 볼트 노트</span>
            <span className="text-xs text-brand-400">
              {meta.vault_connected === false ? "· 볼트 미연결" : `· ${meta.related_count ?? 0}개 · 미완료 액션 ${meta.open_actions ?? 0} · 최근 결정 ${meta.recent_decisions ?? 0}`}
            </span>
          </div>
          {meta.vault_connected === false ? (
            <div className="flex items-start gap-2 text-xs text-amber-700 bg-amber-50 border border-amber-100 rounded-lg px-3 py-2">
              <AlertTriangle size={14} className="mt-0.5 shrink-0" />
              <span>Obsidian 볼트가 연결되지 않아 관련 노트를 찾지 못했습니다. [설정] → Obsidian 볼트 폴더를 지정하면 관련 기록을 근거로 브리핑이 풍부해집니다.</span>
            </div>
          ) : (meta.related && meta.related.length > 0) ? (
            <ul className="space-y-1">
              {meta.related.map((n, i) => (
                <li key={i} className="flex items-center gap-2 text-xs text-brand-600 bg-zinc-50 border border-zinc-100 rounded-lg px-3 py-1.5">
                  <FileText size={13} className="shrink-0 opacity-60" />
                  <span className="truncate font-medium">{n.title || n.path}</span>
                  {typeof n.score === "number" && <span className="ml-auto shrink-0 text-brand-400">{n.score.toFixed(3)}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <div className="text-xs text-brand-400">관련 노트를 찾지 못했습니다(볼트에 관련 기록이 없을 수 있음).</div>
          )}
        </div>
      )}

      {brief && (
        <div className="mt-4 bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5">
          <div className="flex justify-end gap-2 mb-2">
            <button onClick={copy} className="flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:text-brand-800 bg-brand-50 hover:bg-brand-100 px-3 py-1.5 rounded-lg transition-colors">
              {copied ? <CheckCircle size={14} className="text-emerald-500" /> : <Copy size={14} />} {copied ? "복사됨" : "복사"}
            </button>
            {savedId ? (
              <button
                onClick={() => onSaved?.(savedId)}
                className="flex items-center gap-1.5 text-xs font-bold text-emerald-700 bg-emerald-50 hover:bg-emerald-100 px-3 py-1.5 rounded-lg transition-colors"
              >
                <CheckCircle size={14} /> 저장됨 — 대시보드에서 보기
              </button>
            ) : (
              <button
                onClick={save}
                disabled={saving}
                className="flex items-center gap-1.5 text-xs font-bold text-white bg-brand-950 hover:bg-brand-900 px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50"
              >
                {saving ? <Loader2 size={14} className="animate-spin" /> : <Save size={14} />} 대시보드에 저장
              </button>
            )}
          </div>
          <div className="text-sm max-h-[600px] overflow-y-auto">
            <Markdown content={brief} />
          </div>
        </div>
      )}
    </div>
  );
}

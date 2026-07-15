import React, { useState } from "react";
import { ClipboardList, Loader2, Copy, CheckCircle } from "lucide-react";
import { prepBrief } from "../lib/api";

export default function PrepBrief() {
  const [title, setTitle] = useState("");
  const [topic, setTopic] = useState("");
  const [loading, setLoading] = useState(false);
  const [brief, setBrief] = useState("");
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);

  const run = async () => {
    if (!title.trim()) return;
    setLoading(true);
    setError("");
    setBrief("");
    const res = await prepBrief(title.trim(), topic.trim());
    if (res.ok && res.brief) setBrief(res.brief);
    else setError(res.message || "브리핑을 생성하지 못했습니다.");
    setLoading(false);
  };

  const copy = () => {
    navigator.clipboard?.writeText(brief);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="max-w-3xl mx-auto px-1 md:px-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">회의 준비 브리핑</h2>
      <p className="text-brand-500 mb-4 text-sm">제목/주제로 볼트의 관련 노트·이전 결정·미완료 액션을 모아 준비 자료를 만듭니다.</p>

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

      {brief && (
        <div className="mt-4 bg-white border border-zinc-200 rounded-2xl shadow-sm p-4 md:p-5">
          <div className="flex justify-end mb-2">
            <button onClick={copy} className="flex items-center gap-1.5 text-xs font-medium text-brand-600 hover:text-brand-800 bg-brand-50 hover:bg-brand-100 px-3 py-1.5 rounded-lg transition-colors">
              {copied ? <CheckCircle size={14} className="text-emerald-500" /> : <Copy size={14} />} {copied ? "복사됨" : "복사"}
            </button>
          </div>
          <div className="whitespace-pre-wrap text-sm text-brand-800 leading-relaxed max-h-[600px] overflow-y-auto">{brief}</div>
        </div>
      )}
    </div>
  );
}

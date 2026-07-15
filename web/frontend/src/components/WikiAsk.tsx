import React, { useState } from "react";
import { MessageCircleQuestion, Loader2, Send, AlertTriangle, HelpCircle, FileText } from "lucide-react";
import { askWiki, backendAvailable, type WikiAskResult } from "../lib/api";

interface HistoryItem {
  question: string;
  result?: WikiAskResult;
  error?: string;
}

export default function WikiAsk() {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [backendChecked, setBackendChecked] = useState<boolean | null>(null);

  const checkBackend = async () => {
    if (backendChecked !== null) return backendChecked;
    const ok = await backendAvailable();
    setBackendChecked(ok);
    return ok;
  };

  const handleAsk = async () => {
    const q = question.trim();
    if (!q || asking) return;
    setAsking(true);
    setQuestion("");

    const ok = await checkBackend();
    if (!ok) {
      setHistory((h) => [{ question: q, error: "이 기능은 서버 모드(백엔드 실행)에서만 사용할 수 있습니다. Settings에서 백엔드 URL을 확인해주세요." }, ...h]);
      setAsking(false);
      return;
    }

    try {
      const result = await askWiki(q);
      setHistory((h) => [{ question: q, result }, ...h]);
    } catch (e: any) {
      setHistory((h) => [{ question: q, error: e?.message || "질의에 실패했습니다." }, ...h]);
    } finally {
      setAsking(false);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleAsk();
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0 pb-20 md:pb-0">
      <h2 className="text-3xl md:text-4xl font-bold tracking-tight mb-2">Ask Vault Wiki</h2>
      <p className="text-brand-500 mb-6 md:mb-10 text-sm md:text-base">
        Obsidian Vault에 쌓인 회의·세미나 기록을 근거로 질문에 답합니다.
      </p>

      <div className="bg-white border border-brand-100 md:border-zinc-200 rounded-2xl md:rounded-3xl shadow-sm md:shadow-xl p-5 md:p-8">
        <div className="flex gap-3">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="예: 지난 세미나에서 발표하신 교수님이 누구야?"
            className="flex-1 px-4 md:px-5 py-3 md:py-4 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm md:text-base resize-none min-h-[56px]"
            rows={2}
            disabled={asking}
          />
          <button
            onClick={handleAsk}
            disabled={!question.trim() || asking}
            className="shrink-0 flex items-center justify-center gap-2 px-5 md:px-6 py-3 md:py-4 bg-zinc-900 text-white rounded-xl font-bold hover:bg-zinc-800 transition-all shadow-lg disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
          >
            {asking ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
          </button>
        </div>
        <p className="text-[10px] md:text-xs text-zinc-400 mt-2">Enter로 질문, Shift+Enter로 줄바꿈</p>
      </div>

      <div className="mt-6 md:mt-8 space-y-5">
        {history.length === 0 && !asking && (
          <div className="text-center py-16 text-brand-400">
            <MessageCircleQuestion size={40} className="mx-auto mb-3 opacity-50" />
            <p className="text-sm">아직 질문이 없습니다. Vault 지식을 기반으로 무엇이든 물어보세요.</p>
          </div>
        )}

        {asking && (
          <div className="flex items-center gap-2 text-brand-500 text-sm px-2">
            <Loader2 className="animate-spin" size={16} /> Vault 검색 + 답변 생성 중...
          </div>
        )}

        {history.map((item, i) => (
          <div key={i} className="bg-white border border-brand-100 md:border-zinc-200 rounded-2xl shadow-sm p-5 md:p-6">
            <div className="flex items-start gap-2 mb-4">
              <HelpCircle size={18} className="text-brand-400 mt-0.5 shrink-0" />
              <p className="font-bold text-brand-900">{item.question}</p>
            </div>

            {item.error ? (
              <div className="flex items-start gap-2 text-amber-700 bg-amber-50 border border-amber-100 rounded-xl p-4 text-sm">
                <AlertTriangle size={16} className="mt-0.5 shrink-0" />
                <span>{item.error}</span>
              </div>
            ) : item.result ? (
              <>
                <div className="whitespace-pre-wrap font-medium text-brand-800 leading-relaxed text-sm md:text-base">
                  {item.result.answer}
                </div>

                {(item.result.has_conflict || item.result.unverified) && (
                  <div className="flex flex-wrap gap-2 mt-4">
                    {item.result.has_conflict && (
                      <span className="text-xs font-bold text-amber-700 bg-amber-50 border border-amber-200 px-3 py-1 rounded-full">
                        ⚠ 충돌 정보 있음
                      </span>
                    )}
                    {item.result.unverified && (
                      <span className="text-xs font-bold text-zinc-500 bg-zinc-100 border border-zinc-200 px-3 py-1 rounded-full">
                        확인 불가 항목 있음
                      </span>
                    )}
                  </div>
                )}

                {item.result.sources?.length > 0 && (
                  <div className="mt-5 pt-4 border-t border-brand-100">
                    <p className="text-[10px] font-bold text-zinc-400 uppercase tracking-widest mb-2">
                      컨텍스트 노트 {item.result.sources.length}개
                    </p>
                    <div className="space-y-1.5">
                      {item.result.sources.map((s, si) => (
                        <div key={si} className="flex items-center gap-2 text-xs md:text-sm text-brand-600">
                          <FileText size={13} className="shrink-0 opacity-60" />
                          <span className="truncate">{s.title}{s.heading ? ` — ${s.heading}` : ""}</span>
                          {typeof s.score === "number" && (
                            <span className="ml-auto shrink-0 text-zinc-400">{s.score.toFixed(3)}</span>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

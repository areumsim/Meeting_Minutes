import React, { useState, useEffect } from "react";
import { MessageCircleQuestion, Loader2, Send, AlertTriangle, HelpCircle, FileText, FolderOpen } from "lucide-react";
import { askWiki, backendAvailable, getConfig, type WikiAskResult } from "../lib/api";

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
  // 노트 폴더 연결 여부 — 이 기능은 .md 노트 폴더가 연결돼 있어야 동작한다.
  const [vaultConnected, setVaultConnected] = useState<boolean | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setVaultConnected(!!(cfg?.obsidian?.vault_path || cfg?.indexing?.vault_path));
      } catch {
        setVaultConnected(false);
      }
    })();
  }, []);

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
    // isComposing 가드: 한글 입력 중 후보를 확정하는 Enter 도 keydown 으로 들어온다.
    // 없으면 조합 중인 글자를 확정하려는 Enter 가 질문을 전송해 버리고, 전송 후
    // 입력창을 비우므로 쓰던 문장이 사라진다.
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      handleAsk();
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0 pb-20 md:pb-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1">노트 위키 질문</h2>
      <p className="text-brand-500 mb-6 md:mb-10 text-sm md:text-base">
        연결한 <b>노트 폴더(.md)</b>에 쌓인 회의·세미나 기록을 근거로 질문에 답합니다.
        <br className="hidden md:block" />
        <span className="text-brand-500 text-xs md:text-sm">Obsidian 앱은 필요 없습니다 — [설정] → “노트 폴더(.md)”만 지정하면 됩니다. (지정 후 검색 인덱스는 자동 생성되며, 바로 안 되면 [설정]에서 “검색 인덱스·그래프 재빌드”를 한 번 눌러주세요.)</span>
      </p>

      {vaultConnected === false && (
        <div className="mb-6 flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-2xl p-5 text-sm text-amber-800">
          <FolderOpen size={18} className="mt-0.5 shrink-0" />
          <div>
            <p className="font-bold mb-1">노트 폴더가 연결되지 않았습니다.</p>
            <p className="text-amber-700">이 기능을 쓰려면 먼저 [설정] → <b>노트 폴더(.md)</b>를 지정하세요. 지정 후 검색 인덱스는 자동 생성되며, 바로 안 되면 [검색 인덱스·그래프 재빌드]를 한 번 누르세요. (Obsidian 앱은 필요 없습니다.) 폴더가 연결되면 질문 입력이 활성화됩니다.</p>
          </div>
        </div>
      )}

      <div className={`bg-white border border-brand-100 md:border-zinc-200 rounded-2xl md:rounded-3xl shadow-sm md:shadow-xl p-5 md:p-8 ${vaultConnected === false ? "opacity-60" : ""}`}>
        <div className="flex gap-3">
          <textarea
            aria-label="위키 질문"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={vaultConnected === false ? "노트 폴더를 먼저 연결하세요 ([설정])" : "예: 지난 세미나에서 발표하신 교수님이 누구야?"}
            className="flex-1 px-4 md:px-5 py-3 md:py-4 bg-zinc-50 border border-zinc-200 rounded-xl focus:ring-2 focus:ring-zinc-900 outline-none font-medium text-sm md:text-base resize-none min-h-[56px] disabled:cursor-not-allowed"
            rows={2}
            disabled={asking || vaultConnected === false}
          />
          <button
            onClick={handleAsk}
            disabled={!question.trim() || asking || vaultConnected === false}
            className="shrink-0 flex items-center justify-center gap-2 px-5 md:px-6 py-3 md:py-4 bg-zinc-900 text-white rounded-xl font-bold hover:bg-zinc-800 transition-all shadow-lg disabled:opacity-50 disabled:cursor-not-allowed active:scale-[0.98]"
          >
            {asking ? <Loader2 className="animate-spin" size={18} /> : <Send size={18} />}
          </button>
        </div>
        <p className="text-[11px] md:text-xs text-zinc-500 mt-2">Enter로 질문, Shift+Enter로 줄바꿈</p>
      </div>

      <div className="mt-6 md:mt-8 space-y-5">
        {history.length === 0 && !asking && (
          <div className="text-center py-16 text-brand-500">
            <MessageCircleQuestion size={40} className="mx-auto mb-3 opacity-50" />
            <p className="text-sm">아직 질문이 없습니다. 노트 폴더에 쌓인 기록을 기반으로 무엇이든 물어보세요.</p>
          </div>
        )}

        {asking && (
          <div className="flex items-center gap-2 text-brand-500 text-sm px-2">
            <Loader2 className="animate-spin" size={16} /> 노트 검색 + 답변 생성 중...
          </div>
        )}

        {history.map((item, i) => (
          <div key={i} className="bg-white border border-brand-100 md:border-zinc-200 rounded-2xl shadow-sm p-5 md:p-6">
            <div className="flex items-start gap-2 mb-4">
              <HelpCircle size={18} className="text-brand-500 mt-0.5 shrink-0" />
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
                    <p className="text-[11px] font-bold text-zinc-500 uppercase tracking-widest mb-2">
                      컨텍스트 노트 {item.result.sources.length}개
                    </p>
                    <div className="space-y-1.5">
                      {item.result.sources.map((s, si) => (
                        <div key={si} className="flex items-center gap-2 text-xs md:text-sm text-brand-600">
                          <FileText size={13} className="shrink-0 opacity-60" />
                          <span className="truncate">{s.title}{s.heading ? ` — ${s.heading}` : ""}</span>
                          {typeof s.score === "number" && (
                            <span className="ml-auto shrink-0 text-zinc-500">{s.score.toFixed(3)}</span>
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

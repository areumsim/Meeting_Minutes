import React, { useState, useEffect } from "react";
import { MessageCircleQuestion, Send, FileText, FolderOpen, AlertTriangle } from "lucide-react";
import { askWiki, backendAvailable, getConfig, type WikiAskResult } from "../../lib/api";
import { submitOnEnter } from "../../lib/useSubmitOnEnter";
import { Button } from "../../ui/Button";
import { Banner } from "../../ui/Banner";
import { Textarea } from "../../ui/Field";
import { Tag } from "../../ui/StatusPill";
import { Spinner, EmptyState } from "../../ui/states";

interface HistoryItem {
  question: string;
  result?: WikiAskResult;
  error?: string;
}

/**
 * 노트 폴더 질문(RAG) — PRD FR-KNO-2.
 *
 * 게이트가 둘이다: **노트 폴더 미연결**(설정 문제)과 **백엔드 없음**(단독 모드). 둘을 같은
 * 문구로 뭉치면 사용자가 고칠 수 없는 쪽을 고치려 든다 — 각각 다르게 안내한다.
 *
 * 답변에는 출처(컨텍스트 노트)를 반드시 붙이고, 서버가 표시한 ⚠충돌·확인불가를 그대로
 * 낸다(`has_conflict`/`unverified`). 근거 없는 확답처럼 보이면 안 된다.
 */
export default function AskPanel() {
  const [question, setQuestion] = useState("");
  const [asking, setAsking] = useState(false);
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [backendOk, setBackendOk] = useState<boolean | null>(null);
  const [vaultConnected, setVaultConnected] = useState<boolean | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const cfg = await getConfig();
        setVaultConnected(!!(cfg?.obsidian?.vault_path || cfg?.indexing?.vault_path));
      } catch { setVaultConnected(false); }
    })();
  }, []);

  const ask = async () => {
    const q = question.trim();
    if (!q || asking || vaultConnected === false) return;
    setAsking(true);
    setQuestion("");

    let ok = backendOk;
    if (ok === null) { ok = await backendAvailable(); setBackendOk(ok); }
    if (!ok) {
      setHistory((h) => [{
        question: q,
        error: "이 기능은 PC 서버가 함께 떠 있을 때만 동작합니다. 모바일 단독 모드에서는 노트 폴더를 검색할 수 없어요 — [더보기] → PC 서버 연결을 확인하세요.",
      }, ...h]);
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

  const gated = vaultConnected === false;

  return (
    <div className="mx-auto w-full max-w-4xl space-y-3">
      {gated && (
        <Banner icon={FolderOpen} title="노트 폴더가 연결되지 않았습니다.">
          [설정] → <b>노트 폴더(.md)</b>를 지정하면 이 기능이 켜집니다.{" "}
          <b>Obsidian 앱은 필요 없습니다</b> — .md 파일이 들어 있는 폴더면 됩니다. 지정하면
          검색 인덱스가 자동 생성되고, 바로 안 되면 [검색 인덱스·그래프 재빌드]를 한 번 누르세요.
        </Banner>
      )}

      <div className="flex gap-2">
        <Textarea
          aria-label="노트 폴더에 질문"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={submitOnEnter(ask)}
          disabled={asking || gated}
          rows={2}
          className="w-full"
          placeholder={gated ? "노트 폴더를 먼저 연결하세요 ([설정])"
            : "예: 지난 세미나에서 발표한 분이 누구였지?"}
        />
        <Button variant="primary" icon={Send} busy={asking} onClick={ask}
          disabled={!question.trim() || gated} className="shrink-0 self-start h-[3.25rem]">
          질문
        </Button>
      </div>
      <p className="text-xs text-ink-3">Enter 로 질문, Shift+Enter 로 줄바꿈</p>

      {asking && <Spinner label="노트를 찾고 답을 만드는 중" />}

      {history.length === 0 && !asking && (
        <EmptyState icon={MessageCircleQuestion} title="아직 질문이 없습니다"
          description="노트 폴더에 쌓인 회의·세미나 기록을 근거로 무엇이든 물어보세요." />
      )}

      {history.map((item, i) => (
        <article key={i} className="rounded-card border border-line bg-surface shadow-card">
          <h3 className="border-b border-line px-3.5 py-2 text-base font-semibold text-ink">
            {item.question}
          </h3>
          <div className="px-3.5 py-3">
            {item.error ? (
              <p role="alert" className="flex items-start gap-2 rounded-card border border-warn-line
                bg-warn-bg px-3 py-2 text-sm text-warn">
                <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
                {item.error}
              </p>
            ) : item.result ? (
              <>
                <p className="ko-text whitespace-pre-wrap text-base text-ink-2">{item.result.answer}</p>

                {(item.result.has_conflict || item.result.unverified) && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {item.result.has_conflict && (
                      <span className="inline-flex items-center gap-1 rounded-full border border-warn-line
                        bg-warn-bg px-2 py-0.5 text-xs font-semibold text-warn">
                        <AlertTriangle size={11} aria-hidden="true" /> 노트끼리 어긋나는 내용이 있습니다
                      </span>
                    )}
                    {item.result.unverified && <Tag>노트에서 확인하지 못한 항목이 있습니다</Tag>}
                  </div>
                )}

                {/* 출처는 선택이 아니다 — 노트 기반 답변인데 근거가 없으면 그냥 LLM 이 지어낸
                    문장과 구분되지 않는다. */}
                {item.result.sources?.length > 0 && (
                  <div className="mt-3 border-t border-line pt-2">
                    <p className="mb-1 text-xs font-semibold text-ink-3">
                      컨텍스트 노트 {item.result.sources.length}개
                    </p>
                    <ul className="space-y-0.5">
                      {item.result.sources.map((s, si) => (
                        <li key={si} className="flex items-center gap-1.5 text-sm text-ink-2">
                          <FileText size={12} className="shrink-0 text-ink-3" aria-hidden="true" />
                          <span className="truncate">{s.title}{s.heading ? ` — ${s.heading}` : ""}</span>
                          {typeof s.score === "number" && (
                            <span className="num ml-auto shrink-0 text-xs text-ink-3">{s.score.toFixed(3)}</span>
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </>
            ) : null}
          </div>
        </article>
      ))}
    </div>
  );
}

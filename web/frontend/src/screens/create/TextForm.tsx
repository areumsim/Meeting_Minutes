import React, { useEffect, useRef, useState } from "react";
import { FileText, ClipboardPaste } from "lucide-react";
import { processTextInput, getCostSummary, getCostRates } from "../../lib/api";
import { MODE_PRESETS } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Banner } from "../../ui/Banner";
import { Field, Textarea } from "../../ui/Field";
import CostConfirmModal from "../../ui/CostConfirmModal";
import MetaFields, { emptyMeta, type Meta } from "./MetaFields";
import ModePanel from "./ModePanel";

/**
 * 텍스트 분석 (PRD §6.2 · 매트릭스 1-C).
 *
 * 두 가지가 업로드와 다르다:
 *
 * 1. **금액은 서버가 준 값만 쓴다.** 이 경로에는 업로드 같은 `confirm_required` 2단계
 *    계약이 없지만, 드는 돈은 회의록 생성 LLM 1회로 고정이고 그 금액이 이미
 *    `/api/cost/rates` 의 `minutes_flat`(= `pricing.minutes_cost()`)으로 나온다. 그래서
 *    글자수로 추정을 지어내지 않고 그 숫자를 그대로 보여준다 — 서버의 한도 판정도
 *    같은 값을 쓰므로 화면과 서버가 어긋나지 않는다.
 *
 * 2. **번역이 적용되지 않는다.** 그 엔드포인트는 language·translate 를 받지 않는다. 모드
 *    요약에서 언어·번역 줄을 빼는 이유다 — 화면이 "번역 영어→한국어"라고 적어도 실제로는
 *    아무 일도 일어나지 않는다.
 */
export default function TextForm({ onComplete }: { onComplete: (id: string) => void }) {
  const [text, setText] = useState("");
  const [meta, setMeta] = useState<Meta>(emptyMeta);
  const [modeNum, setModeNum] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const [spend, setSpend] = useState<{ mtd: number; cap: number } | null>(null);
  // 서버가 계산한 회의록 생성 1회 비용. 못 받으면(구버전·백엔드 없음) 금액 줄을 비운다 —
  // 화면이 대신 계산하지 않는다.
  const [estimateUsd, setEstimateUsd] = useState<number | undefined>(undefined);
  // 문서 전체에서 첫 textarea 를 찾던 코드를 ref 로 바꾼다 — 이 화면에는 textarea 가
  // 둘(주제·본문)이라 예전 방식은 엉뚱한 칸에 포커스를 줬다.
  const bodyRef = useRef<HTMLTextAreaElement>(null);

  const preset = MODE_PRESETS[modeNum] || MODE_PRESETS[1];

  useEffect(() => {
    getCostSummary().then((s) => s && setSpend({ mtd: s.monthToDateUsd, cap: s.monthlyCapUsd }));
    getCostRates().then((r) => r && setEstimateUsd(r.minutes_flat));
  }, []);

  const paste = async () => {
    try {
      const clip = await navigator.clipboard?.readText?.();
      if (clip) { setText((prev) => prev + (prev ? "\n" : "") + clip); return; }
    } catch { /* iOS Safari 등은 클립보드 읽기를 막는다 */ }
    // 붙여넣기를 대신 해 줄 수 없으면 최소한 커서를 그 자리에 둔다.
    bodyRef.current?.focus();
    setError("브라우저가 붙여넣기를 막았습니다 — 본문 칸에서 Ctrl+V(길게 눌러 붙여넣기)를 쓰세요.");
  };

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await processTextInput(text, {
        title: meta.title, topic: meta.topic, type: preset.type,
      });
      setConfirming(false);
      onComplete(data.sessionId);
    } catch (err) {
      setError(err instanceof Error ? err.message
        : "[설정]에서 API 키가 입력돼 있는지 확인한 뒤 다시 시도해 주세요.");
      setBusy(false);
      setConfirming(false);
    }
  };

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_290px]">
      {confirming && (
        <CostConfirmModal
          what="붙여넣은 글을 회의록·요약·액션으로 정리합니다 — 생성 AI 호출 1회분 비용이 듭니다."
          estimateUsd={estimateUsd}
          targets={[{ label: "본문 길이", value: `${text.length.toLocaleString()}자` }]}
          monthToDateUsd={spend?.mtd}
          monthlyCapUsd={spend?.cap}
          confirmLabel="분석 시작"
          busy={busy}
          onCancel={() => setConfirming(false)}
          onConfirm={run}
        />
      )}

      <div className="space-y-3">
        {error && <Banner tone="err" title="처리하지 못했습니다" onDismiss={() => setError("")}>{error}</Banner>}

        <MetaFields value={meta} onChange={setMeta} disabled={busy} showSpeakers={false}
          titlePlaceholder="예: 팀 회의 메모" />

        <Field label="본문" htmlFor="text-body"
          hint={`${text.length.toLocaleString()}자`}
          description="회의 메모나 전사 텍스트를 붙여넣으세요.">
          <div className="space-y-1.5">
            <Textarea id="text-body" ref={bodyRef} value={text} disabled={busy} rows={10}
              placeholder="회의 메모, 전사 텍스트 등을 여기에 붙여넣으세요…"
              onChange={(e) => setText(e.target.value)} />
            <Button variant="secondary" size="sm" icon={ClipboardPaste} onClick={paste} disabled={busy}>
              붙여넣기
            </Button>
          </div>
        </Field>

        <Button variant="primary" icon={FileText} className="w-full" busy={busy}
          disabled={!text.trim()} onClick={() => setConfirming(true)}>
          분석 &amp; 문서 생성
        </Button>
      </div>

      <ModePanel modeNum={modeNum} onChange={setModeNum} disabled={busy} translation={false}
        hint="붙여넣은 글에는 음성 인식·번역이 적용되지 않습니다 — 유형만 회의록 형식에 반영됩니다." />
    </div>
  );
}

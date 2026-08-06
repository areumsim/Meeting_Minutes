import React from "react";
import Modal from "./Modal";
import { Button } from "./Button";
import { fmtUsd } from "../lib/costEstimate";

/**
 * 과금 시작 전 확인 (PRD FR-NEW-3 · FR-AST-2 · §9).
 *
 * 과금이 시작되는 진입점은 넷이다 — 업로드 · 텍스트 · 노트 첨부 오디오 배치 · 계획 자동화.
 * 그런데 **서버가 예상 금액을 주는 곳은 업로드 하나뿐**이다:
 *   · `POST /api/upload` 가 `confirm_required` + `estimateUsd` + `pendingId` 를 준다.
 *   · `/api/process-text` 는 금액도 한도 검사도 없다.
 *   · 볼트 오디오·계획 자동화는 `spend_guard.blocked()` 로 거절만 하고 금액을 주지 않는다.
 *
 * 그래서 변형이 둘이다. **없는 금액을 프런트가 지어내지 않는다** — 단가 표를 두 번째로
 * 만들면 서버 단가가 바뀔 때 화면만 옛 숫자를 말한다(이 리포가 반복해 없앤 형태).
 * 금액이 없을 때는 "무엇에 과금되는지" + **서버가 준** 이번 달 지출·한도를 보여주고
 * 동의를 받는다. 그것이 이 모달의 목적(사전 동의 + 한도 가시성)에는 충분하다.
 *
 * 백드롭 클릭으로는 닫지 않는다(Modal 기본) — 업로드 경로에서 오클릭 한 번에 올린 파일이
 * 서버에서 지워지고 대용량을 처음부터 다시 올려야 했던 사고를 막은 판단을 유지한다.
 * Escape 는 닫는다(취소와 같은 동작).
 */

export function CostConfirmModal({
  title = "예상 비용 확인",
  what,
  estimateUsd,
  durationSec,
  targets,
  monthToDateUsd,
  monthlyCapUsd,
  confirmLabel = "계속 처리",
  busy,
  onCancel,
  onConfirm,
}: {
  title?: string;
  /** 무엇에 돈이 드는지 한 줄. 예: "이 파일을 처리하면 음성 인식·번역·회의록 생성 비용이 듭니다." */
  what: React.ReactNode;
  /** 서버가 준 예상 금액. **없으면 금액 줄을 만들지 않는다**(추정하지 않는다). */
  estimateUsd?: number;
  durationSec?: number;
  /** 대상 건수처럼 금액 대신 규모를 알려 주는 값. 예: {label: "처리 대상", value: "4건(미처리 2)"} */
  targets?: { label: string; value: React.ReactNode }[];
  monthToDateUsd?: number;
  monthlyCapUsd?: number;
  confirmLabel?: string;
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cap = monthlyCapUsd ?? 0;
  const mtd = monthToDateUsd ?? 0;
  // 한도 초과 판정 — 금액을 아는 경우엔 합계로, 모르는 경우엔 이미 넘겼는지로만 본다.
  // (서버가 최종 판정이다. 여기서 막는 것은 헛걸음을 줄이기 위한 것이지 방어선이 아니다.)
  const blocked = cap > 0 && (estimateUsd != null ? mtd + estimateUsd > cap : mtd >= cap);

  return (
    <Modal labelledBy="cost-confirm-title" onClose={onCancel}
      panelClassName="w-[26rem] max-w-[92vw] rounded-card border border-line-strong bg-surface shadow-pop">
      <h3 id="cost-confirm-title" className="border-b border-line px-4 py-2.5 text-md font-semibold text-ink">
        {title}
      </h3>

      <div className="space-y-2 px-4 py-3 text-sm">
        <p className="text-ink-2">{what}</p>

        <dl className="space-y-1">
          {durationSec != null && durationSec > 0 && (
            <Row label="길이 (약)" value={`${Math.max(1, Math.round(durationSec / 60))}분`} />
          )}
          {targets?.map((t) => <Row key={t.label} label={t.label} value={t.value} />)}
          {estimateUsd != null ? (
            <Row label="이 작업 예상" strong
              value={estimateUsd > 0 ? fmtUsd(estimateUsd, 2) : "산정 불가"} />
          ) : (
            // 금액을 모른다는 사실 자체를 적는다. 빈칸으로 두면 "$0" 으로 읽힌다.
            <Row label="이 작업 예상" value={<span className="text-ink-3">미리 계산되지 않음</span>} />
          )}
          {cap > 0 && (
            <Row label="이번 달 지출 / 한도" value={`${fmtUsd(mtd, 2)} / ${fmtUsd(cap, 2)}`} />
          )}
        </dl>

        {blocked && (
          <p role="alert" className="rounded-ctl border border-rec bg-rec-bg px-2 py-1.5 text-sm text-rec">
            이번 달 지출 한도를 넘습니다 — 시작할 수 없습니다.
            [설정] → 지출 한도에서 한도를 조정하세요.
          </p>
        )}
        <p className="text-xs text-ink-3">
          금액은 설정된 모델 단가 기준 <b>대략치</b>입니다. 실제 청구액과 다를 수 있습니다.
        </p>
      </div>

      <div className="flex justify-end gap-2 border-t border-line px-4 py-2.5">
        <Button variant="secondary" size="sm" onClick={onCancel}>취소</Button>
        <Button variant="primary" size="sm" onClick={onConfirm} busy={busy} disabled={blocked}>
          {confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}

function Row({ label, value, strong }: { label: string; value: React.ReactNode; strong?: boolean }) {
  return (
    <div className={`flex items-baseline justify-between gap-4 ${strong ? "font-semibold text-ink" : "text-ink-2"}`}>
      <dt>{label}</dt>
      <dd className="num">{value}</dd>
    </div>
  );
}

export default CostConfirmModal;

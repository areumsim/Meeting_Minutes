/**
 * 녹음 러닝 비용 미터 — **총액과 항목 내역을 한 함수에서** 만든다.
 *
 * 왜 함수로 묶었나 — 종전 Recorder 는 헤더 합계를 `stt_effective_per_min`(보정 포함 실효
 * 단가)로 계산하면서, 바로 아래 펼침 내역에는 `stt_per_min` 과 `revise_per_min` 을 **따로**
 * 적었다. 두 표시가 서로 다른 식에서 나오니 항목을 더해도 총액이 안 나올 수 있었다.
 * 이 리포가 반복해서 없애 온 형태(단가 표 4곳·노트 판정 2곳)의 프런트판이라 여기서 끝낸다:
 * `CostMeter`(총액)와 `CostBreakdown`(항목)은 **같은 반환값**을 그린다.
 *
 * 이 파일은 단가 표를 갖지 않는다 — 단가는 서버 `/api/cost/rates`(pricing.py)가 정본이고
 * 여기서는 받은 요율에 시간을 곱할 뿐이다. 새 과금 종류를 화면이 지어내지 않는다.
 */

import type { CostRates } from "./api";

export interface CostItem {
  key: string;
  label: string;
  usd: number;
  /** 분당 요율(있으면 "$0.006/분"을 함께 보여준다). 건수 기반 항목엔 없다. */
  ratePerMin?: number;
  /** 추정이 아니라 서버가 실제로 계산해 보낸 발생분인지(개입 카드). */
  actual?: boolean;
}

export interface CostEstimate {
  items: CostItem[];
  total: number;
}

export interface CostEstimateInput {
  rates: CostRates;
  durationSec: number;
  /** 이 녹음이 번역 모드인지(MODE_PRESETS 의 translate). */
  translate: boolean;
  /** 서버가 각 개입에 실어 보낸 costUsd 의 합 — 시간 비례가 아니라 건수 기반이다. */
  facilitationUsd?: number;
  /** 위 금액에 해당하는 개입 건수(내역 문구용). */
  facilitationCount?: number;
  /** 회의록 생성비(minutes_flat)는 정지 이후에만 더한다 — 녹음 중에는 아직 안 쓴 돈이다. */
  includeMinutes: boolean;
}

/**
 * 항목과 합계를 함께 만든다. `items.reduce(+usd) === total` 이 항상 성립한다
 * (테스트가 이걸 고정한다 — 이 파일의 존재 이유다).
 */
export function estimateRunningCost(input: CostEstimateInput): CostEstimate {
  const { rates, durationSec, translate, includeMinutes } = input;
  const minutes = Math.max(0, durationSec) / 60;
  const items: CostItem[] = [];

  // 1차 인식과 2단계 보정.
  // `stt_effective_per_min` 은 보정 패스까지 포함한 실효 단가다(구버전 백엔드엔 없다).
  // 보정분을 **차액으로** 구하면 항목 합이 실효 단가와 정확히 일치한다 — 두 값을 각각
  // 곱해 더하면 서버가 다른 방식으로 실효 단가를 낸 경우 합이 어긋난다.
  const first = num(rates.stt_per_min);
  const effective = rates.stt_effective_per_min != null
    ? num(rates.stt_effective_per_min)
    : first + (rates.two_pass ? num(rates.revise_per_min) : 0);
  const revisePerMin = Math.max(0, effective - first);

  items.push({
    key: "stt",
    label: revisePerMin > 0 ? "1차 인식" : "음성 인식",
    usd: first * minutes,
    ratePerMin: first,
  });
  if (revisePerMin > 0) {
    items.push({
      key: "stt_revise",
      // 표기 통일: 코드·화면·문서 모두 "2단계 보정"(PRD §10). '2패스'로 쓰지 않는다.
      label: "2단계 보정",
      usd: revisePerMin * minutes,
      ratePerMin: revisePerMin,
    });
  }

  if (translate) {
    items.push({
      key: "translate",
      label: "번역",
      usd: num(rates.translate_per_min) * minutes,
      ratePerMin: num(rates.translate_per_min),
    });
  }

  // 진행 도우미의 주기 트리아지(분당). 기능이 꺼져 있으면 서버가 0을 준다.
  const facPerMin = num(rates.facilitation_per_min);
  if (facPerMin > 0) {
    items.push({
      key: "facilitation",
      label: "진행 도우미 점검",
      usd: facPerMin * minutes,
      ratePerMin: facPerMin,
    });
  }

  // 개입 카드는 시간이 아니라 건수다 — 실제로 뜬 카드의 서버 계산 금액만 더한다.
  const facUsd = num(input.facilitationUsd);
  if (facUsd > 0) {
    const n = input.facilitationCount ?? 0;
    items.push({
      key: "facilitation_cards",
      label: n > 0 ? `진행 도우미 개입 ${n}건` : "진행 도우미 개입",
      usd: facUsd,
      actual: true,
    });
  }

  if (includeMinutes) {
    items.push({
      key: "minutes",
      label: "회의록 생성",
      usd: num(rates.minutes_flat),
    });
  }

  return { items, total: items.reduce((sum, it) => sum + it.usd, 0) };
}

/** 녹음 중에는 아직 안 쓴 회의록 생성비를 뺀 금액을 보여준다 — 그 구분을 한 곳에 둔다. */
export const includesMinutesCost = (status: string): boolean =>
  status === "generating" || status === "completed";

const num = (v: unknown): number => {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
};

/** 금액 표기 — 소액이 많아 기본 3자리. 0 은 "$0.000" 으로 그대로 보여준다(숨기지 않는다). */
export const fmtUsd = (usd: number, digits = 3): string =>
  `$${(Number.isFinite(usd) ? usd : 0).toFixed(digits)}`;

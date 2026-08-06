import { describe, expect, it } from "vitest";
import { estimateRunningCost, includesMinutesCost, fmtUsd } from "./costEstimate";
import type { CostRates } from "./api";

/**
 * 이 파일이 고정하려는 것은 단가가 아니라 **한 가지 성질**이다:
 * 화면에 보이는 항목을 더하면 화면에 보이는 총액이 나온다.
 *
 * 종전 Recorder 는 헤더 합계를 `stt_effective_per_min` 으로, 펼침 내역을
 * `stt_per_min` + `revise_per_min` 으로 각각 계산했다 — 서버가 실효 단가를 다른 식으로
 * 내면 둘이 어긋나고, 사용자는 "계산이 틀렸다"로 읽는다. 그 갈라짐의 회귀 테스트다.
 */

const rates = (over: Partial<CostRates> = {}): CostRates => ({
  stt_model: "gpt-4o-mini-transcribe",
  stt_per_min: 0.003,
  translate_per_min: 0.002,
  minutes_flat: 0.05,
  ...over,
});

const sum = (items: { usd: number }[]) => items.reduce((s, i) => s + i.usd, 0);
/** 확정 항목만(= 지금까지 쓴 돈). pending 은 "완료 시" 예고라 합계에 안 들어간다. */
const sumFixed = (items: { usd: number; pending?: boolean }[]) =>
  items.reduce((s, i) => s + (i.pending ? 0 : i.usd), 0);

describe("항목 합 == 총액", () => {
  it("기본(보정·번역·페르소나 없음)", () => {
    const r = estimateRunningCost({
      rates: rates(), durationSec: 600, translate: false, includeMinutes: false,
    });
    expect(sumFixed(r.items)).toBeCloseTo(r.total, 10);
    expect(sum(r.items)).toBeCloseTo(r.projectedTotal, 10);
    expect(r.total).toBeCloseTo(0.03, 10);          // 10분 × $0.003
    expect(r.projectedTotal).toBeCloseTo(0.08, 10); // + 완료 시 회의록 생성 $0.05
  });

  it("2단계 보정 + 번역 + 개입 + 회의록 생성이 모두 있을 때", () => {
    const r = estimateRunningCost({
      rates: rates({ two_pass: true, stt_per_min: 0.003, revise_per_min: 0.006,
                     stt_effective_per_min: 0.009, facilitation_per_min: 0.001 }),
      durationSec: 3600, translate: true,
      facilitationUsd: 0.0123, facilitationCount: 4, includeMinutes: true,
    });
    expect(sumFixed(r.items)).toBeCloseTo(r.total, 10);
    // 정지 후에는 회의록 생성비가 확정분이라 두 합계가 같다.
    expect(r.projectedTotal).toBeCloseTo(r.total, 10);
  });

  it("서버 실효 단가가 부분 합과 다르면 **실효 단가를 따른다**", () => {
    // 보정 모델이 바뀌어 서버가 revise_per_min 과 다른 실효 단가를 낸 경우.
    // 화면은 서버 값을 총액으로 쓰고, 보정 항목을 차액으로 맞춰 합을 일치시킨다.
    const r = estimateRunningCost({
      rates: rates({ two_pass: true, stt_per_min: 0.003, revise_per_min: 0.006,
                     stt_effective_per_min: 0.010 }),
      durationSec: 60, translate: false, includeMinutes: false,
    });
    expect(r.total).toBeCloseTo(0.010, 10);
    expect(sumFixed(r.items)).toBeCloseTo(r.total, 10);
    expect(r.items.find((i) => i.key === "stt_revise")?.usd).toBeCloseTo(0.007, 10);
  });

  it("구버전 백엔드(실효 단가 필드 없음)에서도 보정분이 빠지지 않는다", () => {
    const r = estimateRunningCost({
      rates: rates({ two_pass: true, revise_per_min: 0.006, stt_effective_per_min: undefined }),
      durationSec: 60, translate: false, includeMinutes: false,
    });
    expect(r.total).toBeCloseTo(0.009, 10);
    expect(sumFixed(r.items)).toBeCloseTo(r.total, 10);
  });
});

describe("항목 구성", () => {
  it("보정이 없으면 '1차 인식'이 아니라 '음성 인식'으로 적는다(없는 단계를 암시하지 않게)", () => {
    const r = estimateRunningCost({
      rates: rates(), durationSec: 60, translate: false, includeMinutes: false,
    });
    expect(r.items.map((i) => i.label)).toEqual(["음성 인식", "회의록 생성 (완료 시)"]);
  });

  it("표기는 '2단계 보정' 으로 통일한다(‘2패스’ 금지 — PRD §10)", () => {
    const r = estimateRunningCost({
      rates: rates({ two_pass: true, revise_per_min: 0.006 }),
      durationSec: 60, translate: false, includeMinutes: false,
    });
    const labels = r.items.map((i) => i.label);
    expect(labels).toContain("2단계 보정");
    expect(labels.join(" ")).not.toMatch(/2패스|2-pass/i);
  });

  it("개입은 추정이 아니라 실측이라 표시한다 — 시간을 늘려도 금액이 변하지 않는다", () => {
    const short = estimateRunningCost({
      rates: rates(), durationSec: 60, translate: false,
      facilitationUsd: 0.02, facilitationCount: 3, includeMinutes: false,
    });
    const long = estimateRunningCost({
      rates: rates(), durationSec: 6000, translate: false,
      facilitationUsd: 0.02, facilitationCount: 3, includeMinutes: false,
    });
    const pick = (r: typeof short) => r.items.find((i) => i.key === "facilitation_cards")!;
    expect(pick(short).usd).toBe(0.02);
    expect(pick(long).usd).toBe(0.02);
    expect(pick(short).actual).toBe(true);
    expect(pick(short).label).toContain("3건");
  });

  it("녹음 중에는 회의록 생성비를 합계에 더하지 않는다(아직 안 쓴 돈)", () => {
    expect(includesMinutesCost("recording")).toBe(false);
    expect(includesMinutesCost("generating")).toBe(true);
    expect(includesMinutesCost("completed")).toBe(true);
  });

  it("[회귀] 녹음 중에도 '완료 시 생성' 항목은 목록에 남는다", () => {
    // FR-REC-1 이 내역에 요구하는 항목이다. 목록에서 아예 빼면 정지 버튼을 누르는 순간
    // 금액이 뛰는 이유를 알 수 없다 — 재구현 중 실제로 한 번 사라졌던 자리다.
    const r = estimateRunningCost({
      rates: rates(), durationSec: 60, translate: false, includeMinutes: false,
    });
    const minutes = r.items.find((i) => i.key === "minutes");
    expect(minutes?.pending).toBe(true);
    expect(minutes?.label).toContain("완료 시");
    expect(minutes?.usd).toBeCloseTo(0.05, 10);
    // 그런데 지금 합계에는 안 들어간다.
    expect(r.total).toBeLessThan(r.projectedTotal);
  });

  it("요율이 비어 있어도(구버전·백엔드 없음) NaN 을 화면에 내보내지 않는다", () => {
    const r = estimateRunningCost({
      rates: { stt_model: "", stt_per_min: undefined as any, translate_per_min: undefined as any,
               minutes_flat: undefined as any },
      durationSec: 120, translate: true, includeMinutes: true,
    });
    expect(Number.isFinite(r.total)).toBe(true);
    expect(r.items.every((i) => Number.isFinite(i.usd))).toBe(true);
  });
});

describe("fmtUsd", () => {
  it("0 을 숨기지 않는다 — 빈칸은 '아직 안 쌓였다'가 아니라 '모른다'로 읽힌다", () => {
    expect(fmtUsd(0)).toBe("$0.000");
  });
  it("자릿수를 지정할 수 있다", () => {
    expect(fmtUsd(1.23456, 2)).toBe("$1.23");
  });
});

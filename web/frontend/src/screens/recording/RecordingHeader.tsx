import React from "react";
import { formatClock } from "../../lib/format";
import { estimateRunningCost, includesMinutesCost } from "../../lib/costEstimate";
import { CostMeter } from "../../ui/CostMeter";
import { StatusPill, Tag } from "../../ui/StatusPill";
import type { CostRates } from "../../lib/api";

/**
 * 녹음 헤더 (PRD FR-REC-1) — 상태 · 타이머 · VU · 파형 · 러닝 비용 미터.
 *
 * 비용은 `lib/costEstimate` **한 함수**에서 총액과 항목을 함께 받는다. 종전에는 헤더 합계와
 * 펼침 내역이 서로 다른 식이라 항목을 더해도 총액이 안 나올 수 있었다.
 *
 * 파형은 실제 입력 볼륨 기반이다 — 예전 Math.random() 파형은 무음에도 춤춰서 "소리가
 * 들어가고 있다"는 착시를 줬다. 동작 줄이기에서는 index.css 전역 규칙이 멈춘다.
 */
export default function RecordingHeader({
  status, isPaused, duration, volume, soundDetected, rates, translate,
  facilitationUsd, facilitationCount, modeLabel, captureLabel, connectionNote,
}: {
  status: string;
  isPaused: boolean;
  duration: number;
  /** 0~100 근사(RMS). 파형·VU 막대의 원천. */
  volume: number;
  soundDetected: boolean;
  rates: CostRates | null;
  translate: boolean;
  facilitationUsd: number;
  facilitationCount: number;
  modeLabel: string;
  captureLabel: string;
  /** 평문 연결 상태 한 줄(WS/HTTP·폴백은 숨긴다, FR-REC-3). */
  connectionNote: string;
}) {
  const recording = status === "recording";
  const cost = rates
    ? estimateRunningCost({
        rates, durationSec: duration, translate,
        facilitationUsd, facilitationCount,
        includeMinutes: includesMinutesCost(status),
      })
    : null;

  return (
    <header className={`mb-2 rounded-card border px-3 py-2 ${
      recording && !isPaused ? "border-rec bg-rec-bg" : "border-line bg-surface"}`}>
      <div className="flex flex-wrap items-center gap-2">
        {recording ? (
          <StatusPill tone={isPaused ? "warn" : "rec"} pulse={!isPaused}>
            {isPaused ? "일시정지" : "녹음 중"}
          </StatusPill>
        ) : status === "generating" ? (
          <StatusPill tone="proc" pulse>회의록 생성 중</StatusPill>
        ) : status === "completed" ? (
          <StatusPill tone="ok">완료</StatusPill>
        ) : status === "connecting" ? (
          <StatusPill tone="proc" pulse>연결 중</StatusPill>
        ) : (
          <StatusPill tone="idle">준비됨</StatusPill>
        )}

        <Tag>{modeLabel}</Tag>
        <Tag>{captureLabel}</Tag>

        <div className="flex-1" />

        {cost && (recording || status === "generating" || status === "completed") && (
          <CostMeter total={cost.total} items={cost.items} projectedTotal={cost.projectedTotal}
            label="이번 회의" note="대략치" />
        )}

        <span className={`num text-xl font-bold tabular-nums ${recording ? "text-rec" : "text-ink"}`}>
          {formatClock(duration)}
        </span>
      </div>

      {recording && (
        <div className="mt-1.5 flex items-end gap-[3px]" aria-hidden="true">
          {/* 파형 24바 — 가운데가 높은 형태에 실제 볼륨을 곱한다. */}
          {Array.from({ length: 24 }, (_, i) => {
            const level = Math.min(volume / 40, 1);
            const shape = 0.35 + 0.65 * Math.sin(((i + 1) / 25) * Math.PI);
            const h = isPaused ? 3 : 3 + level * 22 * shape;
            return (
              <span key={i} style={{ height: `${h}px` }}
                className={`w-1 rounded-full transition-[height] duration-150 ${
                  isPaused ? "bg-line-strong" : soundDetected ? "bg-rec" : "bg-line-strong"}`} />
            );
          })}
        </div>
      )}

      {/* 평문 한 줄 — WS/HTTP·제공자 폴백 같은 구현은 여기 오지 않는다(§10). */}
      {connectionNote && (
        <p className="mt-1 text-xs text-ink-3" aria-live="polite">{connectionNote}</p>
      )}
    </header>
  );
}

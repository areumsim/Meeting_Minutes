import React, { useEffect, useState } from "react";
import { ChevronDown } from "lucide-react";
import { getCostSummary, type CostSummary as Summary } from "../../lib/api";
import { typeLabel, statusLabel } from "../../lib/format";
import { kindLabel } from "../../lib/costKinds";
import { fmtUsd } from "../../lib/costEstimate";
import type { Session } from "../../lib/types";

/**
 * 라이브러리 상단 비용·현황 요약 (PRD FR-LIB-5).
 *
 * 설계 제약(기존 CostSummary 에서 이어받는다):
 *  - **차트 라이브러리를 넣지 않는다.** 포터블 zip 용량·오프라인 번들 정책. 막대는
 *    인라인 SVG 로 직접 그린다(GraphView 선례, 의존성 0).
 *  - 조회 실패 시 **비용 카드를 렌더하지 않는다** — 비용 조회 실패로 세션 목록이 깨지면 안 된다.
 *  - 부정확성은 상시 노출한다. 방향("실제보다 크게")까지 적는 게 중요하다 — 방향 없이
 *    "다를 수 있음"만 쓰면 사용자가 한도를 잘못 낮춘다.
 *
 * 프로토타입의 세 번째 카드("연결된 노트 474")는 **만들지 않는다.** 볼트 인덱스 건수를
 * 주는 API 가 없어서 화면이 숫자를 지어내야 한다. 없는 수치를 그리느니 카드를 빼는 쪽이
 * 맞다(있는 척하는 UI 를 만들지 않는다).
 */
export default function CostSummaryCard({ sessions }: { sessions: Session[] }) {
  const [data, setData] = useState<Summary | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => { getCostSummary().then(setData); }, []);

  // 회의 현황은 이미 받아 온 목록에서 센다 — 같은 것을 서버에 다시 묻지 않는다.
  const counts = sessions.reduce<Record<string, number>>((acc, s) => {
    acc[s.status] = (acc[s.status] || 0) + 1;
    return acc;
  }, {});
  const statusLine = ["completed", "processing", "error", "pending"]
    .filter((k) => counts[k])
    .map((k) => `${statusLabel(k)} ${counts[k]}`)
    .join(" · ");

  const cap = data?.monthlyCapUsd ?? 0;
  const mtd = data?.monthToDateUsd ?? 0;
  const ratio = cap > 0 ? Math.min(1, mtd / cap) : 0;
  const overCap = cap > 0 && mtd >= cap;
  const overHalf = cap > 0 && ratio >= 0.5;

  return (
    <section className="mb-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {data && (
          <div className="rounded-card border border-line bg-surface p-3 shadow-card">
            <p className="text-xs text-ink-3">이번 달 예상 비용</p>
            <p className="num mt-0.5 text-2xl font-bold tracking-tight">{fmtUsd(mtd, 2)}</p>
            {cap > 0 ? (
              <>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-surface-2">
                  <div
                    className={`h-full rounded-full ${
                      overCap ? "bg-rec" : overHalf ? "bg-warn" : "bg-accent"}`}
                    style={{ width: `${ratio * 100}%` }}
                  />
                </div>
                <p className="mt-1 text-xs text-ink-3">
                  한도 {fmtUsd(cap, 2)} 중 {Math.round(ratio * 100)}%
                </p>
              </>
            ) : (
              <p className="mt-1 text-xs text-ink-3">지출 한도 없음 — [설정]에서 정할 수 있어요</p>
            )}
          </div>
        )}

        <div className="rounded-card border border-line bg-surface p-3 shadow-card">
          <p className="text-xs text-ink-3">회의</p>
          <p className="num mt-0.5 text-2xl font-bold tracking-tight">{sessions.length}</p>
          <p className="mt-1 text-xs text-ink-3">{statusLine || "아직 없습니다"}</p>
        </div>
      </div>

      {data && (
        <>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-expanded={open}
            className="mt-1.5 inline-flex items-center gap-1 rounded-ctl px-1.5 py-0.5 text-sm text-ink-2 hover:bg-hover"
          >
            비용 자세히
            <ChevronDown size={13} aria-hidden="true"
              className={`transition-transform ${open ? "" : "-rotate-90"}`} />
          </button>

          {open && (
            <div className="mt-1.5 space-y-3 rounded-card border border-line bg-surface p-3">
              {data.months.length > 0 && <MonthlyBars months={data.months} />}

              {data.byType.length > 0 && (
                <Section title="이번 달 유형별">
                  {data.byType.map((t) => (
                    <Row key={t.type} label={`${typeLabel(t.type)} · ${t.count}건`} value={fmtUsd(t.usd, 2)} />
                  ))}
                </Section>
              )}

              {data.otherUsd > 0 && (
                // '회의 외'가 아니라 '세션에 안 잡히는' 지출이다 — 회의 진행 페르소나는
                // 회의 중에 발생하지만 이중 집계를 피해 여기로 들어온다.
                <Section title="세션에 잡히지 않는 지출">
                  {Object.entries(data.otherByKind).map(([kind, usd]) => (
                    <Row key={kind} label={kindLabel(kind)} value={fmtUsd(usd, 4)} />
                  ))}
                </Section>
              )}

              {data.top.length > 0 && (
                <Section title="이번 달 상위 회의">
                  {data.top.map((s) => (
                    <Row key={s.id} label={s.title} value={fmtUsd(s.usd, 2)} />
                  ))}
                </Section>
              )}

              <p className="border-t border-line pt-2 text-xs leading-relaxed text-ink-3">
                모든 금액은 <b>설정된 모델 단가 기준 추정치</b>입니다. 실제 청구액과 다를 수 있으며,
                네트워크 장애로 Groq·로컬 전사로 자동 전환된 회의는 <b>실제보다 크게</b> 표시됩니다
                (전환 이력을 세션 비용에 반영하는 경로가 아직 없습니다). 정확한 금액은 각 API
                제공사 콘솔에서 확인하세요.
              </p>
            </div>
          )}
        </>
      )}
    </section>
  );
}

function MonthlyBars({ months }: { months: { month: string; usd: number; count: number }[] }) {
  const max = Math.max(0.0001, ...months.map((m) => m.usd));
  return (
    <section>
      <h4 className="mb-1 text-xs font-semibold text-ink-3">최근 월별</h4>
      <svg viewBox={`0 0 ${Math.max(1, months.length) * 60} 90`} className="h-[90px] w-full"
        role="img" aria-label="최근 월별 예상 비용">
        {months.map((m, i) => {
          const h = Math.round((m.usd / max) * 52);
          return (
            <g key={m.month} transform={`translate(${i * 60}, 0)`}>
              <rect x={14} y={60 - h} width={32} height={Math.max(1, h)} rx={3}
                fill="var(--color-accent)" opacity={0.75} />
              <text x={30} y={56 - h} textAnchor="middle" fontSize={9} fill="var(--color-ink-2)">
                {fmtUsd(m.usd, 2)}
              </text>
              <text x={30} y={74} textAnchor="middle" fontSize={9} fill="var(--color-ink-3)">
                {m.month.slice(5)}월
              </text>
              <text x={30} y={85} textAnchor="middle" fontSize={8} fill="var(--color-ink-3)">
                {m.count}건
              </text>
            </g>
          );
        })}
      </svg>
    </section>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section>
      <h4 className="mb-1 text-xs font-semibold text-ink-3">{title}</h4>
      <ul className="space-y-0.5">{children}</ul>
    </section>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <li className="flex items-baseline justify-between gap-3 text-sm text-ink-2">
      <span className="truncate">{label}</span>
      <span className="num shrink-0 font-semibold text-ink">{value}</span>
    </li>
  );
}

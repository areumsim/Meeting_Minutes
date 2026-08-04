import React, { useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Wallet } from "lucide-react";
import { getCostSummary, type CostSummary as Summary } from "../lib/api";
import { typeLabel } from "../lib/format";
import { kindLabel } from "../lib/costKinds";

/**
 * 비용 요약 카드 (Dashboard 상단).
 *
 * 백엔드는 이미 다 있었고 화면만 없었다 — pricing.py 단가, month_to_date_spend,
 * 세션별 cost. 사용자는 업로드 확인 다이얼로그에서만 금액을 볼 수 있었다.
 *
 * 설계 제약:
 *  - **새 페이지/라우트를 만들지 않는다.** 사이드바 9개·모바일 탭 6개로 이미 꽉 찼다.
 *  - **차트 라이브러리를 넣지 않는다.** 포터블 zip 용량·오프라인 번들 정책. 막대는
 *    인라인 SVG 로 직접 그린다(MiniGraph.tsx 선례, 의존성 0).
 *  - 조회 실패 시 **카드를 아예 렌더하지 않는다** — 비용 조회 실패로 세션 목록이
 *    깨지면 안 된다.
 */
export default function CostSummary() {
  const [data, setData] = useState<Summary | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => { getCostSummary().then(setData); }, []);

  if (!data) return null;   // 백엔드 없음/실패 — 대시보드 본체를 방해하지 않는다

  const cap = data.monthlyCapUsd;
  const mtd = data.monthToDateUsd;
  const ratio = cap > 0 ? Math.min(1, mtd / cap) : 0;
  const overHalf = cap > 0 && ratio >= 0.5;
  const overCap = cap > 0 && mtd >= cap;

  const maxMonth = Math.max(0.0001, ...data.months.map((m) => m.usd));

  return (
    <div className="mb-4 bg-white border border-brand-200 rounded-xl overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-brand-50 transition-colors text-left"
      >
        <Wallet size={16} className="text-brand-500 shrink-0" />
        <span className="text-sm font-semibold text-brand-900 shrink-0">이번 달 예상 비용</span>
        <span className="text-sm font-bold text-brand-900">${mtd.toFixed(2)}</span>
        {cap > 0 && (
          <>
            <span className="text-xs text-brand-500">/ 한도 ${cap.toFixed(2)}</span>
            <span className="flex-1 h-1.5 bg-brand-100 rounded-full overflow-hidden max-w-[160px]">
              <span
                className={`block h-full rounded-full ${
                  overCap ? "bg-red-500" : overHalf ? "bg-amber-500" : "bg-brand-500"
                }`}
                style={{ width: `${ratio * 100}%` }}
              />
            </span>
          </>
        )}
        <span className="ml-auto text-brand-500 shrink-0">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-1 border-t border-brand-100 space-y-4">
          {data.months.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-brand-500 mb-2">최근 월별</h4>
              {/* 막대 차트 — 라이브러리 없이 SVG 직접 (MiniGraph.tsx 와 같은 방식) */}
              <svg
                viewBox={`0 0 ${Math.max(1, data.months.length) * 60} 90`}
                className="w-full h-[90px]"
                role="img"
                aria-label="최근 월별 예상 비용"
              >
                {data.months.map((m, i) => {
                  const h = Math.round((m.usd / maxMonth) * 52);
                  return (
                    <g key={m.month} transform={`translate(${i * 60}, 0)`}>
                      <rect
                        x={14} y={60 - h} width={32} height={Math.max(1, h)}
                        rx={3} className="fill-brand-400"
                      />
                      <text x={30} y={56 - h} textAnchor="middle" className="fill-brand-500"
                            style={{ fontSize: 9 }}>
                        ${m.usd.toFixed(2)}
                      </text>
                      <text x={30} y={74} textAnchor="middle" className="fill-brand-400"
                            style={{ fontSize: 9 }}>
                        {m.month.slice(5)}월
                      </text>
                      <text x={30} y={85} textAnchor="middle" className="fill-brand-300"
                            style={{ fontSize: 8 }}>
                        {m.count}건
                      </text>
                    </g>
                  );
                })}
              </svg>
            </section>
          )}

          {data.byType.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-brand-500 mb-1.5">이번 달 유형별</h4>
              <ul className="space-y-1">
                {data.byType.map((t) => (
                  <li key={t.type} className="flex justify-between text-xs text-brand-700">
                    <span>{typeLabel(t.type)} · {t.count}건</span>
                    <span className="font-semibold">${t.usd.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.otherUsd > 0 && (
            <section>
              {/* '회의 외'가 아니라 '세션에 안 잡히는' 지출이다 — 회의 진행 페르소나는
                  회의 중에 발생하지만 이중 집계를 피해 여기로 들어온다. */}
              <h4 className="text-xs font-semibold text-brand-500 mb-1.5">세션에 잡히지 않는 지출</h4>
              <ul className="space-y-1">
                {Object.entries(data.otherByKind).map(([kind, usd]) => (
                  <li key={kind} className="flex justify-between text-xs text-brand-700">
                    <span>{kindLabel(kind)}</span>
                    <span className="font-semibold">${usd.toFixed(4)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {data.top.length > 0 && (
            <section>
              <h4 className="text-xs font-semibold text-brand-500 mb-1.5">이번 달 상위 회의</h4>
              <ul className="space-y-1">
                {data.top.map((s) => (
                  <li key={s.id} className="flex justify-between gap-3 text-xs text-brand-700">
                    <span className="truncate">{s.title}</span>
                    <span className="font-semibold shrink-0">${s.usd.toFixed(2)}</span>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/*
            부정확성은 상시 노출한다. 방향("실제보다 크게")까지 적는 게 중요하다 —
            방향 없이 "다를 수 있음"만 쓰면 사용자가 한도를 잘못 낮춘다.
            근거: pricing.py 주석 — 폴백(Groq/로컬) 발생 세션은 사후 재계산 경로가 없다.
          */}
          <p className="text-[11px] leading-relaxed text-brand-500 border-t border-brand-100 pt-3">
            모든 금액은 <b>설정된 모델 단가 기준 추정치</b>입니다. 실제 청구액과 다를 수 있으며,
            네트워크 장애로 Groq·로컬 전사로 자동 전환된 회의는 <b>실제보다 크게</b> 표시됩니다
            (전환 이력을 세션 비용에 반영하는 경로가 아직 없습니다). 정확한 금액은 각 API
            제공사 콘솔에서 확인하세요.
          </p>
        </div>
      )}
    </div>
  );
}

import React, { useState } from "react";
import {
  AlertTriangle, Baby, Check, Compass, GraduationCap, NotebookPen,
  Search, ShieldAlert, Swords, X,
} from "lucide-react";
import type { Facilitation } from "../lib/types";

/**
 * 페르소나 개입 카드 1장 (PRD §19.3).
 *
 * 설계 제약(관련 노트 스트립과 같은 규칙):
 *  - **접힘이 기본.** 가로 스크롤 칩 한 줄 — 읽지 않아도 회의가 굴러가야 한다(§19.1).
 *    펼침은 사용자가 눌렀을 때만. 새 카드가 와도 레이아웃이 밀리지 않는다.
 *  - 색만으로 위험도를 전달하지 않는다 — 아이콘 + 라벨을 병기한다(색약 대응, §19.7).
 *  - `draft` 배지는 **항상** 붙는다. 이건 판정이 아니라 보조 제안이다.
 *  - 새 디자인 시스템을 만들지 않는다(Tailwind 유틸 + lucide, 기존 관례).
 */

/** 위험 티어별 색 — 저위험(정보) sky / 중위험(관점) violet / 고위험(지적) amber. */
const RISK_STYLE: Record<string, { chip: string; icon: string; label: string }> = {
  low: { chip: "border-sky-200 bg-sky-50 hover:border-sky-400", icon: "text-sky-600", label: "정보" },
  medium: { chip: "border-violet-200 bg-violet-50 hover:border-violet-400", icon: "text-violet-600", label: "관점" },
  high: { chip: "border-amber-200 bg-amber-50 hover:border-amber-400", icon: "text-amber-600", label: "지적" },
};

const PERSONA_ICON: Record<string, React.ComponentType<{ className?: string }>> = {
  facilitator: Compass,
  scribe: NotebookPen,
  junior: Baby,
  senior: ShieldAlert,
  domain_expert: GraduationCap,
  devils_advocate: Swords,
  fact_checker: Search,
  critic: AlertTriangle,
};

/** 개입 유형 라벨 — 카드가 무엇을 하려는 건지 한 단어로 알려준다. */
const KIND_LABEL: Record<string, string> = {
  flow: "흐름",
  missing: "놓침",
  question: "질문",
  counterpoint: "반론",
  contrast: "자료 대조",
};

export default function PersonaCard({
  item, onJump, onAck, onDismiss,
}: {
  item: Facilitation;
  onJump?: (span: { t0: number; t1: number }) => void;
  onAck?: (id: string) => void;
  onDismiss?: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const style = RISK_STYLE[item.risk || "low"] || RISK_STYLE.low;
  const Icon = PERSONA_ICON[item.persona] || Compass;

  return (
    <div className={`shrink-0 max-w-[min(22rem,80vw)] border rounded-xl px-2.5 py-1.5 transition-colors ${style.chip}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full text-left"
        title="눌러서 근거 펼치기"
      >
        <div className="flex items-center gap-1.5">
          <Icon className={`w-3.5 h-3.5 shrink-0 ${style.icon}`} />
          <span className="text-[11px] font-bold text-zinc-700 whitespace-nowrap">
            {item.personaLabel}
          </span>
          <span className="text-[10px] text-zinc-400 whitespace-nowrap">
            {KIND_LABEL[item.kind] || item.kind} · {style.label}
          </span>
          {/* 항상 붙는 초안 라벨 — 판정이 아니라 제안이다 */}
          <span className="text-[10px] bg-white/70 border border-zinc-200 text-zinc-500 px-1 rounded whitespace-nowrap">
            초안
          </span>
          {/* 팩트체커·도메인의 신뢰 수준 구분(§6). M1 은 라이브 검색을 쓰지 않는다. */}
          {item.searched === false && (item.persona === "fact_checker" || item.persona === "domain_expert") && (
            <span
              className="text-[10px] bg-white/70 border border-amber-200 text-amber-700 px-1 rounded whitespace-nowrap"
              title="라이브 웹 검증 없이 사내 노트·대화만 근거로 한 제안입니다"
            >
              ⚠ 미검증
            </span>
          )}
        </div>
        <p className={`text-xs text-zinc-700 mt-0.5 ${open ? "" : "line-clamp-1"}`}>
          {item.text}
        </p>
      </button>

      {open && (
        <div className="mt-1.5 pt-1.5 border-t border-white/80 space-y-1.5 max-h-44 overflow-y-auto">
          {item.quote && (
            <p className="text-[11px] text-zinc-500 italic">“{item.quote}”</p>
          )}
          {(item.evidence || []).length > 0 && (
            <ul className="space-y-0.5">
              {(item.evidence || []).map((e, i) => (
                <li key={`${e.title}-${i}`} className="text-[11px] text-zinc-600">
                  {e.source === "web" ? "🌐" : "📄"} <b>{e.title}</b>
                  {typeof e.score === "number" && e.score > 0 && (
                    <span className="text-zinc-400"> · {e.score.toFixed(3)}</span>
                  )}
                  {e.snippet && <span className="text-zinc-500"> — {e.snippet}</span>}
                </li>
              ))}
            </ul>
          )}
          <div className="flex items-center gap-2 pt-0.5">
            {item.span && typeof item.span.t0 === "number" && onJump && (
              <button
                type="button"
                onClick={() => onJump(item.span!)}
                className="text-[11px] text-zinc-500 hover:text-zinc-800 font-medium"
              >
                ⟲ 발화 보기
              </button>
            )}
            {onAck && (
              <button
                type="button"
                onClick={() => onAck(item.id)}
                className="text-[11px] text-emerald-700 hover:text-emerald-900 font-medium flex items-center gap-0.5"
                title="봤습니다 — 카드를 닫습니다"
              >
                <Check className="w-3 h-3" /> 확인
              </button>
            )}
            {onDismiss && (
              <button
                type="button"
                onClick={() => onDismiss(item.id)}
                className="text-[11px] text-zinc-400 hover:text-zinc-700 font-medium flex items-center gap-0.5"
                title="필요 없습니다"
              >
                <X className="w-3 h-3" /> 닫기
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

import React, { useState } from "react";
import {
  AlertTriangle, Baby, Check, ClipboardList, Compass, GraduationCap, NotebookPen,
  Search, ShieldAlert, Swords, X,
} from "lucide-react";
import type { Facilitation } from "../lib/types";
import { sourceStyle } from "../lib/entities";

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

/**
 * 위험 티어 — 정보(persona) / 관점(proc) / 지적(warn). 전부 토큰이라 다크에서도 따라간다.
 * 색은 보조 신호일 뿐이고 `label` 이 함께 나간다(색약 대응, §19.7) — 그래서 sky 같은
 * 2번째 파랑을 새로 만들지 않고 이미 있는 보조 시스템색을 쓴다(PRD §5.1 리뷰 P2-4).
 */
const RISK_STYLE: Record<string, { chip: string; icon: string; label: string }> = {
  low: { chip: "border-line bg-persona-bg hover:border-persona", icon: "text-persona", label: "정보" },
  medium: { chip: "border-line bg-proc-bg hover:border-proc", icon: "text-proc", label: "관점" },
  high: { chip: "border-warn-line bg-warn-bg hover:border-warn", icon: "text-warn", label: "지적" },
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
  summarizer: ClipboardList,
};

/** 개입 유형 라벨 — 카드가 무엇을 하려는 건지 한 단어로 알려준다. */
const KIND_LABEL: Record<string, string> = {
  flow: "흐름",
  missing: "놓침",
  question: "질문",
  counterpoint: "반론",
  contrast: "자료 대조",
  brief: "중간 요약",
};

// 근거 출처 아이콘·라벨은 `lib/entities.ts` 하나를 본다 — 종전에는 여기(EVIDENCE_ICON)와
// Recorder(SOURCE_ICON)가 같은 개념의 표를 따로 갖고 있었다. 이모지는 쓰지 않는다:
// Windows 버전마다 렌더가 다르고 스크린리더가 "서류철"처럼 읽는다(PRD §5.3).

/** 카드에 펼쳐 보일 근거 줄 수. 지난 회의 기록은 결정 5 + 액션 5 까지 올 수 있어
 *  전부 그리면 카드가 화면을 덮는다. 넘치는 건수는 숨기지 않고 숫자로 알린다. */
const EVIDENCE_MAX = 5;

/** 중간 요약 절 — 서버 `facilitation.BRIEF_SECTIONS` 와 같은 순서·같은 말. */
const BRIEF_SECTIONS: { key: keyof NonNullable<Facilitation["brief"]>; label: string }[] = [
  { key: "points", label: "논점" },
  { key: "decisions", label: "결정" },
  { key: "actions", label: "액션" },
  { key: "open_questions", label: "미결 질문" },
];

export default function PersonaCard({
  item, onJump, onAck, onDismiss,
}: {
  item: Facilitation;
  onJump?: (span: { t0: number; t1: number }) => void;
  onAck?: (id: string) => void;
  onDismiss?: (id: string) => void;
}) {
  // 중간 요약은 눌러서 펼치기 전이라도 한 줄로는 쓸모가 없다(절이 4개다) — 처음부터
  // 펼친 상태로 둔다. 대신 카드 폭은 같아서 레인의 시각 리듬은 유지된다.
  const isBrief = item.kind === "brief";
  const [open, setOpen] = useState(isBrief);
  const style = RISK_STYLE[item.risk || "low"] || RISK_STYLE.low;
  const Icon = PERSONA_ICON[item.persona] || Compass;
  const sections = isBrief
    ? BRIEF_SECTIONS.filter((s) => (item.brief?.[s.key] || []).length > 0)
    : [];

  return (
    // 인스펙터(세로 목록) 안에 놓인다 — 폭은 컨테이너를 따른다. 종전 가로 레인 시절의
    // 고정 최대폭을 남겨 두면 320px 패널에서 카드가 잘린다.
    <div className={`w-full rounded-card border px-2.5 py-1.5 transition-colors ${style.chip}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full text-left"
        title="눌러서 근거 펼치기"
      >
        <div className="flex items-center gap-1.5">
          <Icon className={`w-3.5 h-3.5 shrink-0 ${style.icon}`} />
          <span className="text-xs font-bold text-ink whitespace-nowrap">
            {item.personaLabel}
          </span>
          <span className="text-xs text-ink-3 whitespace-nowrap">
            {KIND_LABEL[item.kind] || item.kind} · {style.label}
          </span>
          {/* 항상 붙는 초안 라벨 — 판정이 아니라 제안이다 */}
          <span className="text-xs bg-surface/70 border border-line-strong text-ink-3 px-1 rounded whitespace-nowrap">
            초안
          </span>
          {/* 팩트체커·도메인의 신뢰 수준 구분(§6). M1 은 라이브 검색을 쓰지 않는다. */}
          {item.searched === false && (item.persona === "fact_checker" || item.persona === "domain_expert") && (
            <span
              className="inline-flex items-center gap-0.5 whitespace-nowrap rounded border border-warn-line bg-surface/70 px-1 text-xs text-warn"
              title="라이브 웹 검증 없이 사내 노트·대화만 근거로 한 제안입니다"
            >
              <AlertTriangle size={10} aria-hidden="true" /> 미검증
            </span>
          )}
          {/* 주기 자동 요약과 사용자가 누른 요약을 구분한다(과금 계기가 다르다) */}
          {isBrief && item.onDemand && (
            <span className="text-xs bg-surface/70 border border-line-strong text-ink-3 px-1 rounded whitespace-nowrap">
              지금 정리
            </span>
          )}
        </div>
        {sections.length > 0 ? (
          <div className={`mt-1 space-y-0.5 ${open ? "" : "max-h-10 overflow-hidden"}`}>
            {sections.map((s) => (
              <p key={s.key} className="text-xs text-ink leading-snug">
                <b className="text-ink-3">{s.label}</b>{" "}
                {(item.brief?.[s.key] || []).join(" · ")}
              </p>
            ))}
          </div>
        ) : (
          <p className={`text-xs text-ink mt-0.5 ${open ? "" : "line-clamp-1"}`}>
            {item.text}
          </p>
        )}
      </button>

      {open && (
        <div className="mt-1.5 pt-1.5 border-t border-line space-y-1.5 max-h-44 overflow-y-auto">
          {item.quote && (
            <p className="text-xs text-ink-3 italic">“{item.quote}”</p>
          )}
          {(item.evidence || []).length > 0 && (
            /* 근거는 서버가 한 목록으로 준다(노트·논문·지난 회의 기록·웹). 지난 회의
               기록(🗂)까지 여기 오는 이유는 "이전과 다르다"는 카드가 무엇과 대조했는지
               보여야 하기 때문이다(§6-5) — 종전엔 프롬프트에만 있어 화면에 없었다.
               EVIDENCE_MAX 로 잘라 카드가 길어지지 않게 한다(지난 결정 5 + 액션 5 가
               올 수 있다). 남은 건수는 아래에 숫자로 알린다 — 조용히 감추지 않는다. */
            <ul className="space-y-0.5">
              {(item.evidence || []).slice(0, EVIDENCE_MAX).map((e, i) => {
                const src = sourceStyle(e.source);
                const SrcIcon = src.icon;
                return (
                <li
                  key={`${e.title}-${i}`}
                  className="flex items-baseline gap-1 text-xs text-ink-2"
                  title={e.segment ? `"${e.segment}" 발화에서 검색된 결과` : undefined}
                >
                  {/* 아이콘은 장식이 아니다 — 무엇과 대조했는지가 정보라 라벨을 함께 낸다
                      (노트/논문/지난 회의/웹이 같은 그림으로 보이면 구분이 사라진다). */}
                  <SrcIcon size={11} className="shrink-0 translate-y-0.5" aria-hidden="true" />
                  <span className="sr-only">{src.label}: </span>
                  <b>{e.title}</b>
                  {/* 다른 발화에서 나간 웹 검색이면 그 사실을 밝힌다 — 이 카드의
                      주장을 '검증된 것'으로 읽지 않게 한다(searched 배지와 같은 근거). */}
                  {e.source === "web" && e.matched === false && (
                    <span className="text-ink-3"> (다른 발화)</span>
                  )}
                  {typeof e.score === "number" && e.score > 0 && (
                    <span className="text-ink-3"> · {e.score.toFixed(3)}</span>
                  )}
                  {e.snippet && <span className="text-ink-3"> — {e.snippet}</span>}
                </li>
                );
              })}
              {(item.evidence || []).length > EVIDENCE_MAX && (
                <li className="text-xs text-ink-3">
                  … 그 밖에 {(item.evidence || []).length - EVIDENCE_MAX}건
                </li>
              )}
            </ul>
          )}
          <div className="flex items-center gap-2 pt-0.5">
            {item.span && typeof item.span.t0 === "number" && onJump && (
              <button
                type="button"
                onClick={() => onJump(item.span!)}
                className="text-xs text-ink-3 hover:text-ink font-medium"
              >
                ⟲ 발화 보기
              </button>
            )}
            {onAck && (
              <button
                type="button"
                onClick={() => onAck(item.id)}
                className="text-xs text-ok hover:text-ok font-medium flex items-center gap-0.5"
                title="봤습니다 — 카드를 닫습니다"
              >
                <Check className="w-3 h-3" /> 확인
              </button>
            )}
            {onDismiss && (
              <button
                type="button"
                onClick={() => onDismiss(item.id)}
                className="text-xs text-ink-3 hover:text-ink font-medium flex items-center gap-0.5"
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

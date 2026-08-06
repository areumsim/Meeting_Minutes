import React from "react";
import PersonaCard from "./PersonaCard";
import { Button } from "./Button";
import type { Facilitation, FacilitationStatus } from "../lib/types";

/**
 * 진행 도우미 패널 — 인스펙터의 두 번째 탭 (PRD FR-REC-6·FR-REC-7, §19.1~§19.5).
 *
 * 종전에는 전사 위에 가로로 흐르는 '레인'이었다. 재설계에서 관련 노트·근거·페르소나는
 * 전부 우측 인스펙터로 모으므로(§3-4) 세로 목록이 된다 — **표시 규칙은 그대로다.**
 *
 * 이 기능의 위험은 정확도가 아니라 방해다. 회의 중 화면이 밀리거나, 끌 수 없거나, 초안이
 * 판정처럼 보이면 사용자는 끄고 다시 안 켠다(§18). 그래서:
 *  1. 낼 게 없으면 **아무것도 렌더하지 않는다**(빈 패널이 자리를 먹지 않게).
 *  2. 카드는 접힘이 기본 — 펼침은 사용자가 눌렀을 때만.
 *  3. 끄는 버튼은 항상 보인다. 끄면 서버 **생성**까지 멈춘다(표시만 끄는 게 아니다).
 *  4. 한도·예산으로 멈춘 사유는 조용히 사라지지 않는다.
 *  5. 팩트체커(확인 필요)는 **상단 고정** — 지적은 흘려보내면 안 되는 유일한 종류다(FR-REC-7).
 */

/** 상단에 고정할 페르소나 — 사실 오류 지적은 스크롤 아래로 밀리면 의미가 없다. */
const PINNED_PERSONAS = new Set(["fact_checker"]);

export default function PersonaPanel({
  items, status, pending, muted, briefOn, briefBusy,
  onCheckNow, onBriefNow, onMute, onJump, onAck, onDismiss,
}: {
  items: Facilitation[];
  status?: FacilitationStatus | null;
  pending?: number;
  muted?: boolean;
  /** 중간 요약이 켜져 있는지(서버 실효값) — [지금 정리] 버튼의 표시 조건 */
  briefOn?: boolean;
  briefBusy?: boolean;
  onCheckNow?: () => void;
  onBriefNow?: () => void;
  onMute?: () => void;
  onJump?: (span: { t0: number; t1: number }) => void;
  onAck?: (id: string) => void;
  onDismiss?: (id: string) => void;
}) {
  const notice = status && status.kind !== "pending" ? status.message : "";
  // [지금 정리]가 가능하면 패널을 띄운다 — 버튼 자체가 내용이다. 그 외에는 낼 것이
  // 없으면 렌더하지 않는다.
  if (!items.length && !notice && !pending && !muted && !(briefOn && onBriefNow))
    return null;

  const pinned = items.filter((i) => PINNED_PERSONAS.has(String(i.persona)));
  const rest = items.filter((i) => !PINNED_PERSONAS.has(String(i.persona)));

  return (
    <div className="flex flex-col gap-2">
      {/* 소리를 낼 수 있는데 안 내는 게 아니라, **낼 수단이 없다**. 참견도 4·5(알림음·음성)는
          미구현이고 리포에 소리 재생 코드가 0건이다. 사용자가 "소리 어떻게 끄냐"를 찾아
          헤매지 않도록 화면이 먼저 말한다. */}
      <span
        className="self-start text-xs text-ink-3"
        title="이 기능은 소리를 내지 않습니다 — 알림음·음성(참견도 4·5)은 아직 구현되지 않았습니다. 카드는 조용히 나타납니다."
      >
        소리 없음
      </span>

      {muted ? (
        /* 서버가 개입·중간 요약 생성을 멈춘 상태다(표시만 끄는 것이 아니다) — 문구도 그렇게
           적는다. 다르게 적으면 껐다고 믿은 뒤에도 비용이 남는 줄 알거나, 반대로 판정
           기록까지 멈춘 줄 안다. */
        <p
          className="rounded-ctl border border-line bg-surface-2 px-2 py-1.5 text-xs text-ink-3"
          title="개입 카드와 중간 요약 생성을 멈춰 추가 비용이 발생하지 않습니다. 판정 기록(오탐률 실측용)은 계속됩니다. 다음 녹음에서 다시 켜집니다."
        >
          이번 회의 끔 — 개입 생성을 멈췄습니다
        </p>
      ) : (
        <>
          {pinned.length > 0 && (
            <div className="space-y-1.5">
              <p className="text-xs font-bold text-rec">확인 필요 {pinned.length}</p>
              {pinned.map((it) => (
                <PersonaCard key={it.id} item={it} onJump={onJump} onAck={onAck} onDismiss={onDismiss} />
              ))}
            </div>
          )}

          {rest.length > 0 && (
            <div className="space-y-1.5">
              {pinned.length > 0 && <p className="text-xs font-bold text-ink-3">제안</p>}
              {rest.map((it) => (
                <PersonaCard key={it.id} item={it} onJump={onJump} onAck={onAck} onDismiss={onDismiss} />
              ))}
            </div>
          )}

          {notice && (
            // role="status": 한도·예산으로 멈춘 사유가 조용히 사라지면 안 된다는 규칙(§19.5)의
            // 스크린리더판 — 시각 칩만으로는 낭독되지 않는다.
            <p role="status" title={notice}
              className="rounded-ctl border border-warn-line bg-warn-bg px-2 py-1.5 text-xs text-warn">
              {notice}
            </p>
          )}
        </>
      )}

      {/* 하단 컨트롤 — 인스펙터 푸터에 놓이는 경우 호출부가 `footer` 로 옮겨 담아도 된다.
          여기 함께 두는 이유는 이 세 버튼이 카드와 **같은 계약**이기 때문이다(빌링 투명성). */}
      <div className="flex flex-wrap items-center gap-1.5 pt-1">
        {!muted && !!pending && onCheckNow && (
          <Button size="sm" variant="secondary" onClick={onCheckNow}
            title="모아둔 점검 항목을 지금 보여줍니다(추가 비용 없음)">
            지금 점검 {pending}
          </Button>
        )}
        {/* [지금 정리] — 주기를 기다리지 않고 중간 요약 1회. [지금 점검]과 달리 **새 비용이
            발생**하므로 툴팁에 그 사실을 적는다. */}
        {briefOn && onBriefNow && !muted && (
          <Button size="sm" variant="secondary" onClick={onBriefNow} disabled={briefBusy}
            title="지금까지의 논점·결정·액션을 한 번 정리합니다(요약 LLM 호출 1회 = 소액 비용 발생)">
            {briefBusy ? "정리 중…" : "지금 정리"}
          </Button>
        )}
        {onMute && !muted && (
          <Button size="sm" variant="ghost" onClick={onMute} className="ml-auto"
            title="이번 회의의 개입 카드·중간 요약 생성을 멈춥니다 — 추가 비용이 발생하지 않습니다(설정은 그대로, 다음 녹음에서 다시 켜짐)">
            이번 회의 끔
          </Button>
        )}
      </div>
    </div>
  );
}

import React from "react";
import { Field, Input, Textarea, TextField } from "../../ui/Field";

/**
 * 회의 메타 입력 — 제목 · 참석자 · 주제/맥락 (PRD FR-NEW-2).
 *
 * 세 진입점(녹음·업로드·텍스트)이 같은 값을 받는다. 종전에는 화면마다 라벨과 placeholder 가
 * 조금씩 달랐고(“주제 / 맥락” vs “주제·키워드”), 라벨을 눌러도 포커스가 가지 않는 곳이
 * 있었다 — `Field` 가 id 연결을 맡으므로 그 문제는 구조적으로 사라진다.
 */
export interface Meta {
  title: string;
  speakers: string;
  topic: string;
}

export const emptyMeta: Meta = { title: "", speakers: "", topic: "" };

export default function MetaFields({
  value, onChange, disabled, titlePlaceholder = "예: 주간 제품 회의", showSpeakers = true,
}: {
  value: Meta;
  onChange: (next: Meta) => void;
  disabled?: boolean;
  titlePlaceholder?: string;
  /** 텍스트 분석처럼 참석자가 의미 없는 경로에서는 숨긴다. */
  showSpeakers?: boolean;
}) {
  const set = (patch: Partial<Meta>) => onChange({ ...value, ...patch });
  return (
    <div className="space-y-2.5">
      <TextField label="제목" id="meta-title" value={value.title} disabled={disabled}
        placeholder={titlePlaceholder} onChange={(e) => set({ title: e.target.value })} />

      {showSpeakers && (
        <Field label="참석자" htmlFor="meta-speakers"
          description="이름을 적어 두면 화자 라벨과 회의록 정확도가 좋아집니다.">
          <Input id="meta-speakers" value={value.speakers} disabled={disabled}
            placeholder="예: 홍길동, 김영희, 이철수"
            onChange={(e) => set({ speakers: e.target.value })} />
        </Field>
      )}

      <Field label="주제 / 맥락" htmlFor="meta-topic"
        description="회의 배경을 적으면 용어 인식과 회의록 품질이 올라갑니다.">
        <Textarea id="meta-topic" value={value.topic} disabled={disabled} rows={3}
          placeholder="예: IBM 파트너십 킥오프 — 양자 SW 스택 로드맵"
          onChange={(e) => set({ topic: e.target.value })} />
      </Field>
    </div>
  );
}

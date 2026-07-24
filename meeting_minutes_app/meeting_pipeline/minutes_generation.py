#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
회의록/세미나 기록/강의 노트 LLM 프롬프트 + 생성 로직
(회의록 생성, 스크립트 교정, 액션 아이템 추출, 요약, 화자 이름 추론).
meeting_minutes.py에서 분리 (2026-07 리팩토링 3단계).
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional, Tuple

from meeting_minutes_app.common.llm_client import LLMClient
from meeting_minutes_app.meeting_pipeline.meeting_minutes import (
    MINUTES_MODEL, SUMMARY_MODEL, MAX_LLM_CHARS, TYPE_LABELS,
    logger, step, info, ok, warn, debug_save,
    ts, has_timestamps,
)


def _refresh_config_globals() -> None:
    """config_loader.reload() 훅 — from-import 로 복사된 모델 전역을
    웹 UI 설정 저장 시 재시작 없이 갱신한다(meeting_minutes 훅이 먼저 실행됨)."""
    global MINUTES_MODEL, SUMMARY_MODEL
    from meeting_minutes_app.meeting_pipeline import meeting_minutes as _mm
    MINUTES_MODEL = _mm.MINUTES_MODEL
    SUMMARY_MODEL = _mm.SUMMARY_MODEL


try:
    from meeting_minutes_app.common import config_loader as _cfg_mod
    _cfg_mod.on_reload(_refresh_config_globals)
except ImportError:
    pass


# ══════════════════════════════════════════════════════════════════
#  프롬프트 템플릿 — 여기를 직접 편집하여 구조·규칙을 변경할 수 있습니다.
#  {prefix} 자리는 주제·일시·지시문이 자동 삽입됩니다 (수정 금지).
# ══════════════════════════════════════════════════════════════════

_MINUTES_MEETING = """\
{prefix}전문 회의록 작성자입니다.
스크립트의 모든 논의 내용을 주제별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 스크립트에 등장하는 모든 논의 주제·결정·수치·일정·고유명사를 누락 없이 반영
2. 개별 발언을 시간순으로 나열하지 말고, **주제별로 종합·정리** (타임스탬프 표기 금지)
3. 수치·일정·고유명사·제품명은 원문 그대로 유지 (의역 금지)
4. 핵심 사실·숫자·결정은 **굵게** 강조
5. 화자·조직·역할을 **추측하거나 지어내지 말 것**. 참석자 명단이 제공되면 그 이름만 사용하고, 특정할 수 없으면 귀속하지 않음. 스크립트에 명시되지 않은 소속·팀·직책·발언자(예: "발언자 A", 가상의 팀명)는 만들지 말 것
6. 메모(추가 메모)가 있으면 논의 내용과 적극 연결하여 반영
7. 전문적·격식 문체, 한국어
8. 인사·잡담·여담·진행상 군더더기 등 비중요 발언은 회의록에 싣지 말 것 — 핵심 논의·결정·액션만 정리
9. **스크립트·메모에 없는 사실/인물/조직/수치/기한은 절대 생성하지 말 것.** 불명확하면 "미정"으로 표기
10. 계약·교육·운영 회의는 배경, 일정, 계약 조건, 비용 청구 범위, 운영 프로세스, 이해관계자별 R&R을 별도 안건으로 반드시 분리
11. 지주사/그룹사/계열사/교육기관/외부업체처럼 운영 주체가 여럿이면 주체별 역할·권한·비용·의사결정 범위를 표나 액션으로 구체화

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [회의 주제] 회의록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **참석자**: (제공된 참석자 명단을 그대로 기재. 명단이 없으면 "미정")
- **안건**
    1. 배경 및 진행 경과
    2. 세부 계약 조건 및 운영 방안
    3. 교육 체계/일정/R&R

---

### 주요 논의 내용

### A. 배경 및 진행 경과

- **협의 배경**
    - 세부 내용
- **선정/전환 경과**
    - 세부 내용

### B. [첫 번째 주요 안건 제목]

- **소주제/논점**
    - 세부 내용 (핵심 수치·사실은 **굵게**)
    - 세부 내용
- **소주제/논점**
    - 세부 내용
    - 개선안: 구체적 방안

### C. [두 번째 주요 안건 제목]

- **소주제/논점**
    - 세부 내용

(안건 수만큼 반복)

### [필요 시] 운영 주체별 R&R

| 주체 | 역할/책임 | 확인 필요 | 후속 액션 |
|---|---|---|---|
| 인재개발원/센터 | 스크립트·메모에 근거한 역할 | 미정/확인 필요 | 담당/기한 |
| 내부 수행사/그룹사 | 스크립트·메모에 근거한 역할 | 미정/확인 필요 | 담당/기한 |
| 지주사/그룹사 | 분리 운영 여부와 범위 | 미정/확인 필요 | 담당/기한 |

---

### 결정 사항(합의/정리된 방향)

1. **결정 요약**: 구체적 내용
   - 배경: 왜 이렇게 결정했는지(근거·전제·논의된 대안)
2. **결정 요약**: 구체적 내용
   - 배경: 왜 이렇게 결정했는지(근거·전제·논의된 대안)

---

### Action Item (담당/기한)

- 구체적 업무 내용 — 담당: (제공된 명단 내 인물, 특정 불가 시 "미정") · 기한: (스크립트에 있으면 명시, 없으면 생략)
- 구체적 업무 내용 — 담당: 미정
  ※ 담당자를 임의의 조직/팀명으로 지어내지 말 것

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 회의 주제와 "회의록"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 참석자
- **제공된 참석자 명단(메타데이터/메모)을 그대로 사용.** 명단에 없는 이름·조직·팀·역할을 새로 만들지 말 것
- 명단이 없거나 화자를 특정할 수 없으면 **"미정"** 으로 표기 ("발언자 A/B/C"나 가상의 팀명 생성 금지)
- 화자 분리(diarization) 정보가 없으면 발언별로 담당자를 추정하지 말 것

### 안건
- 스크립트 전체 흐름에서 주요 주제를 식별하여 번호 목록으로 정리

### 주요 논의 내용
- 안건별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 안건 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 동일 주제에 대한 여러 발언은 하나의 소주제 아래 종합
- 의견 대립이 있으면 양측 입장을 모두 기술
- 질문과 답변은 맥락에 녹여서 기술 (별도 Q:/A: 형식 사용 안 함)

### 결정 사항
- 명시적으로 합의·확정된 사항만 기재
- 번호 목록, 각 항목은 `**핵심 키워드**`: 상세 내용
- 각 결정 항목 아래 들여쓰기로 `- 배경:` 서브불릿을 반드시 추가 — 왜 이렇게 결정했는지(근거·전제·논의된 대안·기각된 대안 등)를 기록. 스크립트에 근거가 드러나지 않으면 `- 배경: 스크립트에 명시되지 않음`으로 표기 (배경을 지어내지 말 것)

### Action Item
- 담당 조직/팀/개인별로 그룹핑 (표 형식 사용 금지)
- `- **[담당자/조직]**` 아래 들여쓰기로 업무 나열
- 기한이 언급되었으면 포함, 없으면 생략

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 스크립트 전체 내용을 충실히 반영하되, 반복·중복 발언은 통합
- 각 안건의 소주제마다 구체적 세부 내용·근거·수치를 충분히 포함할 것
- 안건 하나를 1~2줄로 축약하는 것은 금지 — 소주제별로 세부 불릿을 충실히 작성
- 75분 회의 기준 최소 A4 2~3쪽 이상 분량이 되어야 함"""

_MINUTES_SEMINAR = """\
{prefix}전문 세미나 기록 작성자입니다.
발표 스크립트의 모든 내용을 섹션별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 발표에서 다룬 모든 주제·개념·수치·사례를 누락 없이 반영 — 내용이 풍부할수록 좋음
2. 개별 발언을 시간순으로 나열하지 말고, **섹션/주제별로 종합·정리** (타임스탬프 표기 금지)
3. 기술 용어·수치·고유명사·제품명은 원문 그대로 표기
4. 핵심 개념·수치·결론은 **굵게** 강조
5. 발표자의 중요 문구·설명은 직접 인용("")으로 최대한 많이 보존
6. 메모(추가 메모)가 있으면 해당 섹션과 적극 연결하여 반영
7. 전문적 문체, 한국어
8. **과도한 압축 절대 금지** — 발표자가 설명한 이유·맥락·예시·실험 결과를 모두 포함

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [세미나 주제] 세미나 기록

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **발표자**: 명단·스크립트에 명시된 이름만 기재 (불명확하면 "미정", 소속·역할 임의 생성 금지)
- **참석자**: 제공된 명단을 그대로 기재 (없으면 "미정")
- **주제**: 한줄 요약

---

### 발표 내용

### A. [첫 번째 섹션 제목]

- **소주제/개념**
    - 핵심 개념·주장 및 상세 설명
    - 데이터·수치·예시 (원문 그대로, **핵심 수치는 굵게**)
    - 발표자 주요 발언: "직접 인용"
- **소주제/개념**
    - 세부 내용
    - 중요 슬라이드/도식 내용 (언급된 경우)

### B. [두 번째 섹션 제목]

- **소주제/개념**
    - 세부 내용

(섹션 수만큼 반복)

---

### Q&A

- **질문 주제**
    - 질문 내용 및 발표자 답변 요약 (전사본에 있는 내용만 기록)
- **질문 주제**
    - 질문 내용 및 답변

⚠️ **Q&A 작성 규칙 (엄수)**: 전사본에 실제 Q&A 내용이 있을 때만 이 섹션을 작성. 전사본이 종료 전 끊겼거나 Q&A 내용이 없으면 → "⚠️ Q&A 미캡처 (녹음 종료)" 한 줄만 작성. 없는 내용을 추론하거나 만들어내는 것은 절대 금지.

---

### 핵심 인사이트

- 실무에 즉시 적용 가능한 포인트 (발표자가 강조한 내용 중심)
- 주요 시사점
- 기존 기술·연구와의 차별점

---

### 검토 권고사항

- **미해결 질문 / 후속 연구**: Q&A 또는 발표에서 제기됐으나 해결되지 않은 문제점·한계
- **검증 필요 항목**: 수치·사실이 명확히 확인되지 않은 주장 ([검증 필요] 표시)
- **실무·연구 적용 시 주의사항**: 한계점, 전제 조건, 기술 성숙도 주의점
- **다음 단계 제안**: 심화 학습에 필요한 논문, 시도해볼 실험, 추가로 공부할 개념

---

### 참고 자료

#### 📌 발표에서 언급된 자료
- (발표자가 직접 인용·소개한 논문·도구·링크. 형식: **저자 연도**: 제목 — 학술지/URL)
- 없으면 "없음"

#### 💡 관련 심화 자료 (LLM 내부 지식)
- (발표 주제를 더 깊이 이해하는 데 도움이 되는 핵심 논문·자료. 발표에서 언급 여부 무관)
- 형식: **저자 연도**: 제목 — 학술지 | 이 세미나와의 연결점 한 줄
- 실제 존재하는 논문만 최소 3~5개. 불확실하면 기재하지 말 것.

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 세미나 주제와 "세미나 기록"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 발표 내용
- 섹션별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 섹션 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 동일 주제에 대한 여러 설명은 하나의 소주제 아래 종합

### Q&A
- 질문-답변을 주제별로 정리 (맥락에 녹여서 기술)

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 발표 전체 내용을 충실히 반영하되, 반복·중복 설명은 통합
- **각 소주제마다 최소 3~5개 세부 불릿 작성** — 핵심 개념·이유·예시·결과를 모두 포함
- 소주제를 1~2줄로 축약하는 것은 절대 금지 — 교수님이 설명한 배경·맥락·의의를 충분히 기록
- 발표자의 말 중 중요한 설명·비유·강조는 직접 인용으로 최대한 보존
- 실험 결과·수치·데이터셋 이름·벤치마크 지표는 구체적으로 기록
- **30분 발표 기준 최소 A4 3~4쪽, 60분 기준 6~8쪽 이상 분량이 되어야 함**
- 이론적 배경이 있는 경우 수식·알고리즘·개념 간 관계도 충분히 설명"""

_MINUTES_LECTURE = """\
{prefix}전문 강의 노트 작성자입니다.
강의 스크립트의 모든 내용을 챕터/주제별로 정리·종합하여, 빠짐없이 체계적으로 기록하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 강의에서 다룬 모든 개념·예시·공식·논리 흐름을 누락 없이 반영
2. 개별 발언을 시간순으로 나열하지 말고, **챕터/주제별로 종합·정리** (타임스탬프 표기 금지)
3. 수치·공식·코드·고유명사는 원문 그대로 표기
4. 핵심 개념·공식·결론은 **굵게** 강조
5. 강사의 중요 문구는 직접 인용("")으로 표기
6. 메모(추가 메모)가 있으면 해당 개념과 적극 연결하여 반영
7. 전문적이되 이해하기 쉬운 문체, 한국어

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [강의 주제] 강의 노트

- **일시**: YYYY.MM.DD(요일) HH:MM ~
- **장소**: (언급된 경우 기재, 없으면 항목 생략)
- **강사**: 이름 (역할/소속)
- **과목/주제**: 과목명 또는 주제
- **학습 목표**: (강사가 언급한 경우 기재)

---

### 강의 내용

### A. [첫 번째 챕터/주제 제목]

- **핵심 개념**
    - 정의 및 상세 설명
    - 개념의 이유·배경·맥락
- **예시/사례**
    - 강사가 제시한 구체적 사례 (수치·데이터 포함)
    - 실무 적용 방법 (언급된 경우)
- **공식/코드**
    - 원문 그대로 (블록 형식 사용)
    - 강사의 부연 설명
- **강사 발언 인용**
    - "중요 설명 직접 인용"

### B. [두 번째 챕터/주제 제목]

- **핵심 개념**
    - 세부 내용

(챕터 수만큼 반복)

---

### Q&A (학생 질문 & 강사 답변)

- **질문 주제**
    - 질문 내용 및 강사 답변 요약
- **질문 주제**
    - 질문 내용 및 답변

(질문이 없었으면 섹션 생략)

---

### 핵심 정리

- 시험·실무에 중요하다고 강사가 강조한 내용
- 반복 언급된 핵심 포인트

---

### 과제 / 다음 강의 예고

- 언급된 과제 (기한 포함)
- 예습 내용 및 다음 주제

(언급이 없었으면 섹션 생략)

---

### 참고 자료

- 언급된 교재·논문·링크·도구 (원문 표기)

(언급이 없었으면 섹션 생략)

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 강의 주제와 "강의 노트"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 강의 내용
- 챕터별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 각 챕터 아래 `- **소주제**` → 들여쓰기 `- 세부 내용` 계층 구조 사용
- 하나의 개념 설명에 "정의 + 이유/맥락 + 예시"를 모두 포함
- 강사가 반복 강조한 내용은 명시적으로 중요도 표시

### Q&A
- 질문-답변을 주제별로 정리

### 섹션 구분
- 주요 섹션 사이에 `---` 구분선 사용

## 길이 기준 (필수)
- 강의 전체 내용을 충실히 반영하되, 반복·중복 설명은 통합
- 각 챕터의 소주제마다 구체적 세부 내용·근거·예시를 충분히 포함할 것
- 챕터 하나를 1~2줄로 축약하는 것은 금지 — 소주제별로 세부 불릿을 충실히 작성
- 개념 설명을 요약할 때도 이유·예시·논리는 반드시 포함 (과도한 축약 금지)
- 60분 강의 기준 최소 A4 2~3쪽 이상 분량이 되어야 함"""

_SUMMARY_MEETING = """\
{prefix}회의 요약 전문가입니다.
회의록 전문을 읽기 전에 30~60초 안에 판단할 수 있는 executive brief를 작성합니다.
요약은 회의록을 대체하지 않습니다. 세부 논의·근거·발언 흐름은 회의록 본문에 남기고, 여기서는 결론·리스크·후속조치만 압축합니다.

【출력 형식】

### 한눈에 보는 결론
• 회의의 최종 의미를 2~4문장으로 요약
• 확정된 것과 아직 미정인 것을 분리해서 표현

### 결정/합의
• 명확히 확정된 사항만 3~5개 이내
• 없으면 `확정된 결정 없음`이라고 적음

### 리스크/주의
• 사실 확인, 이해관계자 확인, 상충 가능성이 있는 항목만 3~5개 이내
• 추론·참고 배경은 직접 논의된 사실과 구분

### 다음 액션
• 담당자·기한이 명시된 일만 적고, 없으면 담당: 미정
• 5개 이내로 제한

【작성 원칙】
- 전체 400~700자 내외로 압축
- 회의록 본문의 `주요 논의 내용`을 다시 풀어 쓰지 말 것
- 배경 설명·상세 근거·세부 논쟁은 회의록 본문에 맡기고 요약에서는 생략
- 수치·일정·고유명사·제품명은 원문 그대로 유지
- 결정되지 않은 사항은 "미결:" 접두어로 명확히 표시
- 확인되지 않은 참석자·소속·담당자를 지어내지 말 것"""

_SUMMARY_SEMINAR = """\
{prefix}세미나 요약 전문가입니다.
참석하지 않은 동료가 이 요약본 하나만으로 발표 전체를 완전히 파악할 수 있어야 합니다.
이메일로 전송될 내용이므로 **충분한 깊이와 구체성**이 필요합니다.

【출력 형식】

• 일시: / 장소: / 발표자: (기록에 명시된 값 사용)
• 주제 한줄 요약
• 주요 섹션: (번호 목록)

────────────────────────────────────────
배경 / 개요
• 세미나 목적·맥락 (발표자가 설명한 배경, 연구 동기, 문제 제기 포함)
• 발표자 소개 (언급된 경우)

[섹션 1 제목]
• 핵심 주장 및 내용 요약
  ○ 발표자가 설명한 개념의 정의와 배경
  ○ 데이터·수치·실험 결과 (원문 그대로, 구체적으로)
  ○ 핵심 개념 설명 (발표자 표현·인용 포함)
  ○ 왜 중요한지, 어떤 의의가 있는지

[섹션 2 제목]
• … (각 섹션마다 위 구조로 충분히 기록)

────────────────────────────────────────
Q&A 핵심 요약
• 주요 질문과 발표자 답변 (질문 배경·답변 근거 포함)
• 미해결 질문 또는 후속 연구 필요 사항

────────────────────────────────────────
핵심 인사이트 및 시사점
• 발표자가 강조한 핵심 메시지
• 기존 연구/기술과의 차별점
• 향후 방향성 또는 한계점

────────────────────────────────────────
실무/연구 적용 포인트
• 한빝 관련 비즈니스나 연구에 적용 가능한 포인트
• 검토가 필요한 사항 또는 주의점

────────────────────────────────────────
후속 학습 자료
• 발표에서 언급된 논문·도구·링크 (저자/연도 포함)
• 추가 조사 권장 주제

【작성 원칙】
- 각 섹션은 "무엇이 발표됐고, 어떤 근거·실험이 제시됐으며, 왜 중요한가"를 포함
- 수치·고유명사·논문명은 원문 그대로 유지
- **섹션 하나를 1~2줄로 줄이지 말 것** — 핵심 근거·예시·맥락을 반드시 포함
- 압축은 허용하되 개념의 핵심을 제거하는 압축은 금지
- 전체 분량: 발표 길이에 비례 (30분 발표 → 최소 A4 1.5~2쪽)"""

_SUMMARY_LECTURE = """\
{prefix}강의 요약 전문가입니다.
강의에 참석하지 않은 학생이 이 요약본만으로 핵심 개념을 충분히 파악할 수 있어야 합니다.

【출력 형식】

• 강의명: / 강사: / 일시: (기록에 명시된 값 사용)
• 이번 강의 핵심 한줄 요약
• 다룬 챕터: (번호 목록)

────────────────────────────────────────
[챕터/개념 1 제목]
• 핵심 개념 정의 및 설명
  ○ 공식·코드 (원문 그대로, 블록 형식)
  ○ 강사 제시 예시 (구체적으로)
  ○ 이해에 필요한 배경·맥락

[챕터/개념 2 제목]
• …

────────────────────────────────────────
시험/과제 대비 포인트
• 강사가 강조한 내용, 반복 언급 항목 (중요도 표시)

────────────────────────────────────────
질문 & 답변 핵심
• 학생 질문과 강사 답변 요약 (이해에 도움이 되는 것만)

────────────────────────────────────────
다음 강의 준비
• 예습 내용·과제 (기한 포함)

【작성 원칙】
- 각 개념은 "정의 + 이유/맥락 + 예시"를 모두 포함
- 수치·공식·코드·고유명사는 원문 그대로 유지
- 압축은 허용하되 개념의 이유와 예시를 제거하는 압축은 금지"""

_MINUTES_MEMO = """\
{prefix}개인 음성 메모/아이디어 정리 전문가입니다.
스크립트에 담긴 생각의 흐름을 놓치지 않고 주제별로 간결하게 정리하는 것이 핵심 임무입니다.

## 핵심 원칙
1. 형식적인 회의록 틀(참석자·안건 번호 등)을 강요하지 말고, 실제 언급된 주제만 정리
2. 개별 발언을 시간순으로 나열하지 말고, **주제별로 종합·정리** (타임스탬프 표기 금지)
3. 수치·고유명사·용어는 원문 그대로 유지 (의역 금지)
4. 핵심 아이디어·결론은 **굵게** 강조
5. 화자를 추측하거나 지어내지 말 것. 특정할 수 없으면 귀속하지 않음
6. 메모(추가 메모)가 있으면 내용과 적극 연결하여 반영
7. 간결한 문체, 한국어
8. **스크립트·메모에 없는 사실/수치/기한은 절대 생성하지 말 것.** 불명확하면 "미정"으로 표기

## 출력 형식 (이 구조를 정확히 따를 것)

## YYMMDD [메모 주제] 메모 정리

- **일시**: YYYY.MM.DD(요일) HH:MM ~ (알 수 없으면 생략)

---

### 핵심 요약

- 이 메모의 핵심 내용을 2~4문장으로 요약

---

### 세부 내용

### A. [첫 번째 주제]

- **소주제/논점**
    - 세부 내용 (핵심 수치·사실은 **굵게**)

### B. [두 번째 주제]

- **소주제/논점**
    - 세부 내용

(주제 수만큼 반복)

---

### 후속 액션/확인 필요 사항 (있는 경우만)

- 구체적 내용 — 기한이 언급되었으면 명시, 없으면 생략

## 세부 작성 규칙

### 제목
- `## YYMMDD` 형식 (예: 260305), 뒤에 메모 주제와 "메모 정리"
- 주제는 스크립트 도입부·메모·topic 메타정보에서 추론

### 세부 내용
- 주제별로 `### A.`, `### B.`, `### C.` … 알파벳 순서로 소제목 부여
- 동일 주제에 대한 여러 발언은 하나의 소주제 아래 종합

## 길이 기준
- 짧은 메모는 짧게, 긴 메모는 놓치는 내용 없이 충실하게 반영 (형식적 분량 채우기 금지)"""

_SUMMARY_MEMO = """\
{prefix}메모 요약 전문가입니다.
메모 정리본을 읽기 전에 핵심만 빠르게 파악할 수 있도록 짧게 요약합니다.

【출력 형식】

### 한눈에 보는 핵심
• 메모의 핵심 내용을 1~3문장으로 요약

### 확인/후속 필요 사항 (있는 경우만)
• 담당·기한이 명시된 것만 적고, 없으면 생략

【작성 원칙】
- 전체 200~400자 내외로 압축
- 수치·고유명사는 원문 그대로 유지
- 확인되지 않은 사실을 지어내지 말 것"""


# ──────────────────────────────────────────────
#  LLM 프롬프트 조립 (topic / session_dt / no_cut 삽입)
# ──────────────────────────────────────────────
_MINUTES_TEMPLATES = {
    "meeting": _MINUTES_MEETING,
    "seminar": _MINUTES_SEMINAR,
    "lecture": _MINUTES_LECTURE,
    "memo":    _MINUTES_MEMO,
}
_SUMMARY_TEMPLATES = {
    "meeting": _SUMMARY_MEETING,
    "seminar": _SUMMARY_SEMINAR,
    "lecture": _SUMMARY_LECTURE,
    "memo":    _SUMMARY_MEMO,
}

_NO_CUT = ("⚠ 모든 주제·개념·수치·일정·고유명사를 빠짐없이 반영하세요. "
           "주제별로 종합하되, 내용 누락은 금지입니다. "
           "각 소주제마다 충분한 세부 내용을 포함하여 짧은 기록이 되지 않도록 하세요.\n\n")

_NO_CUT_MEETING = ("⚠ 논의된 모든 주제·결정·수치·일정·고유명사를 빠짐없이 반영하세요. "
                   "개별 발언을 나열하지 말고 주제별로 종합하되, 내용 누락은 금지입니다. "
                   "각 소주제마다 충분한 세부 내용을 포함하여 짧은 기록이 되지 않도록 하세요.\n\n")


def _load_external_template(doc_type: str) -> str:
    """analysis.templates_dir의 {doc_type}_analysis.md 오버라이드 템플릿 로드.

    config에 templates_dir가 있고 해당 파일이 존재하면 내장 템플릿 대신
    사용한다 (플레이스홀더: {topic}/{session_dt}/{related_notes}).
    없으면 "" — 내장 템플릿 사용. (과거엔 config 키만 있고 읽는 코드가 없었음)
    """
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        tdir = str(_cfg.get("analysis.templates_dir", "") or "")
        if not tdir:
            return ""
        from pathlib import Path as _P
        path = _P(tdir) / f"{doc_type}_analysis.md"
        if not path.is_absolute():
            path = _P(__file__).resolve().parents[2] / path
        if path.is_file():
            return path.read_text(encoding="utf-8").strip()
    except Exception as e:
        logger.warning(f"[prompt] 외부 템플릿 로드 실패 (내장 사용): {e}")
    return ""


class _SafeFormatDict(dict):
    """외부 템플릿에 없는 플레이스홀더는 빈 문자열로 채움."""

    def __missing__(self, key):
        return ""


def _custom_minutes_block() -> str:
    """config의 analysis.custom_minutes_instructions(사용자 맞춤 지시)를 회의록
    시스템 프롬프트 끝에 우선순위 블록으로 덧붙인다. 비어 있으면 "".

    비개발자가 웹 [설정]에서 원하는 회의록 형식·내용·강조점을 자유롭게 지정할 수
    있게 하는 훅. 기본 템플릿의 형식은 유지하되, 충돌 시 사용자 지시를 우선한다.
    """
    try:
        from meeting_minutes_app.common import config_loader as _cfg
        instr = str(_cfg.get("analysis.custom_minutes_instructions", "") or "").strip()
    except Exception:
        instr = ""
    if not instr:
        return ""
    return (
        "\n\n## ⭐ 사용자 맞춤 지시 (최우선 — 아래 형식/내용 요구를 반드시 반영)\n"
        "위의 기본 출력 형식을 따르되, 아래 사용자 요구가 기본 형식과 충돌하면 "
        "아래 요구를 우선합니다. (단, '사실을 지어내지 말 것' 등 정확성 원칙은 항상 유지)\n\n"
        f"{instr}\n"
    )


def _get_minutes_prompt(doc_type: str, topic: str = "", session_dt: str = "",
                        title: str = "") -> str:
    no_cut = _NO_CUT_MEETING if doc_type == "meeting" else _NO_CUT
    custom = _custom_minutes_block()

    ext = _load_external_template(doc_type)
    if ext:
        head = (f"제목/발표자 힌트: {title}\n\n" if title else "") + no_cut
        # related_notes는 memo(배경 자료)로 별도 주입되므로 빈 값으로 채운다
        return head + ext.format_map(_SafeFormatDict(
            topic=topic or "", session_dt=session_dt or "", related_notes="")) + custom

    tmpl = _MINUTES_TEMPLATES.get(doc_type, "")
    if not tmpl:
        return ""
    prefix = ""
    if title:      prefix += f"제목/발표자 힌트: {title}\n"
    if topic:      prefix += f"주제: {topic}\n"
    if session_dt: prefix += f"일시: {session_dt}\n"
    if prefix:     prefix += "\n"
    prefix += no_cut
    return tmpl.format(prefix=prefix) + custom


def _get_summary_prompt(doc_type: str, topic: str = "", session_dt: str = "") -> str:
    tmpl = _SUMMARY_TEMPLATES.get(doc_type, "")
    if not tmpl:
        return ""
    prefix = ""
    if topic:      prefix += f"주제: {topic}\n"
    if session_dt: prefix += f"일시: {session_dt}\n"
    if prefix:     prefix += "\n"
    return tmpl.format(prefix=prefix)


# ──────────────────────────────────────────────
#  장시간 스크립트 청크 분할 헬퍼
# ──────────────────────────────────────────────
def _split_script_chunks(
    script: str, max_chars: int, overlap: int = 2000
) -> List[str]:
    """타임스탬프 줄 기준으로 스크립트를 max_chars 이하 청크로 분할.
    인접 청크 간 overlap 문자 중첩으로 문맥 연속성 유지.
    """
    lines = script.split('\n')
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # +1 for newline
        if current_len + line_len > max_chars and current:
            chunks.append('\n'.join(current))
            # overlap: 마지막 N자만큼을 다음 청크 시작에 포함
            overlap_lines: List[str] = []
            overlap_total = 0
            for prev_line in reversed(current):
                if overlap_total + len(prev_line) + 1 > overlap:
                    break
                overlap_lines.insert(0, prev_line)
                overlap_total += len(prev_line) + 1
            current = overlap_lines
            current_len = overlap_total
        current.append(line)
        current_len += line_len

    if current:
        chunks.append('\n'.join(current))
    return chunks


def _merge_partial_minutes(
    parts: List[str], llm: LLMClient, doc_type: str
) -> str:
    """복수 파트의 회의록을 하나의 완성된 회의록으로 통합.

    3개 이상이면 cascade 방식으로 2개씩 순차 병합해 압축 손실을 최소화.
    """
    system = (
        "동일 회의/세미나/강의의 여러 파트 기록문서를 하나의 완성된 문서로 통합하세요.\n"
        "규칙:\n"
        "- 중복 헤더·날짜·메타정보만 제거하고, 발표 내용(본문)은 파트별로 최대한 유지\n"
        "- 각 파트의 소제목·소항목·수치·인용 발언·예시 하나도 생략하지 말 것\n"
        "- 시간 순서 유지; 파트 번호 레이블(파트1, 파트2 등)은 최종본에서 제거\n"
        "- Q&A는 모든 파트에서 수집하여 하나의 Q&A 섹션으로 합칠 것\n"
        "- 핵심 인사이트·검토 권고사항·참고 자료도 모든 파트에서 수집·통합\n"
        "- 구성은 표준 세미나/회의록/강의 노트 형식 유지\n"
        "- 요약·압축 금지: 원본에 있는 내용이면 반드시 포함"
    )

    # 3개 이상이면 2개씩 cascade merge로 압축 손실 최소화
    remaining = list(parts)
    while len(remaining) > 2:
        pair_combined = "\n\n---\n\n".join(
            f"## 파트 {i+1}\n{p}" for i, p in enumerate(remaining[:2])
        )
        merged = llm.chat(system, pair_combined, temp=0.2, model=MINUTES_MODEL, max_tokens=16000)
        remaining = [merged] + remaining[2:]

    combined = "\n\n---\n\n".join(
        f"## 파트 {i+1}/{len(remaining)}\n{p}" for i, p in enumerate(remaining)
    )
    return llm.chat(system, combined, temp=0.2, model=MINUTES_MODEL, max_tokens=16000)


# ──────────────────────────────────────────────
#  회의록 / 요약 생성
# ──────────────────────────────────────────────
_MINUTES_REQUIRED_HEADERS = {
    "meeting": ("결정 사항", "Action Item"),
    "seminar": ("핵심 인사이트",),
    "lecture": ("핵심 정리",),
    "memo":    ("핵심 요약",),
}


def _minutes_is_usable(text: Optional[str], script_len: int, doc_type: str) -> Tuple[bool, str]:
    """생성된 회의록이 너무 부실한지(필수 섹션 누락/극단적으로 짧음) 방어적으로 검증한다.
    `_refined_script_is_usable`(교정 단계)과 대칭되는 회의록 생성 단계의 품질 게이트.
    스크립트 자체가 짧으면(500자 미만) 짧은 결과가 정상이므로 게이트를 건너뛴다.
    """
    text = (text or "").strip()
    if not text:
        return False, "회의록 생성 결과가 비어 있음"
    if script_len < 500:
        return True, ""

    section_count = len(re.findall(r"(?m)^###\s", text))
    if section_count < 2:
        return False, f"필수 소제목(### ) 섹션이 부족함 ({section_count}개)"

    missing = [h for h in _MINUTES_REQUIRED_HEADERS.get(doc_type, ()) if h not in text]
    if missing:
        return False, f"필수 섹션 누락: {', '.join(missing)}"

    if len(text) < max(300, int(script_len * 0.03)):
        return False, f"결과가 스크립트 대비 과도하게 짧음 ({len(text)}/{script_len}자)"

    return True, ""


def generate_minutes(
    segments_or_script,   # List[Dict] 또는 교정된 str 텍스트 모두 허용
    llm: LLMClient,
    doc_type: str = "meeting",
    memo: Optional[str] = None,
    debug_dir: Optional[str] = None,
    topic: str = "",
    session_dt: str = "",
    title: str = "",
) -> str:
    labels = TYPE_LABELS[doc_type]
    step(f"{labels['title']} 생성 중...")

    # str이면 교정된 스크립트 텍스트, List[Dict]이면 기존 segments 처리
    if isinstance(segments_or_script, str):
        script = segments_or_script
    else:
        segments = segments_or_script
        use_ts = has_timestamps(segments)
        if use_ts:
            script = "\n".join(
                f"[{ts(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
                for s in segments
            )
        else:
            script = "\n".join(
                f"{s.get('speaker', 'Speaker')}: {s['text']}"
                for s in segments
            )
    logger.debug(f"[MINUTES] 스크립트 {len(script)}자, 타입={doc_type}")

    memo_block = ""
    if memo:
        memo_block = (
            "\n### 내부 참고 메모 (최종 출력 금지)\n"
            "⚠️ 중요: 아래 메모는 발표자/주제에 대한 사전 배경 자료이며, 실제 세미나/회의 발언이 아닙니다.\n"
            "규칙:\n"
            "- 회의록/세미나 기록의 '발표 내용'은 오직 아래 스크립트에서만 파악할 것\n"
            "- 메모/웹리서치에 있는 정보를 발표자가 언급한 것처럼 회의록에 쓰지 말 것\n"
            "- 메모는 전문용어 이해, 발표 맥락 파악, 참고 자료 확인에만 활용\n"
            "- 메모 제목·원문·검색 결과를 회의록에 그대로 출력하지 말 것\n\n"
            f"{memo}\n"
        )
    system = _get_minutes_prompt(doc_type, topic, session_dt, title)
    meta_lines = ""
    if title:      meta_lines += f"### 제목/발표자: {title}\n"
    if session_dt: meta_lines += f"### 녹음 일시: {session_dt}\n"
    if topic:      meta_lines += f"### 주제: {topic}\n"

    if debug_dir:
        debug_save(
            f"{meta_lines}{memo_block}\n### 스크립트:\n{script}",
            os.path.join(debug_dir, "minutes_prompt.txt"),
            "Minutes prompt",
        )

    # MAX_LLM_CHARS 초과 시 청크 분할 처리
    if len(script) > MAX_LLM_CHARS:
        warn(f"스크립트 {len(script):,}자 > {MAX_LLM_CHARS:,}자 → 청크 분할 처리")
        chunks = _split_script_chunks(script, MAX_LLM_CHARS)
        partials: List[str] = []
        for idx, chunk in enumerate(chunks):
            info(f"  청크 {idx+1}/{len(chunks)} ({len(chunk):,}자) 처리 중...")
            chunk_user = (
                f"{meta_lines}{memo_block}\n"
                f"### 스크립트 (파트 {idx+1}/{len(chunks)}):\n{chunk}"
            )
            partials.append(
                llm.chat(system, chunk_user, temp=0.3, model=MINUTES_MODEL, max_tokens=16000)
            )
        result = _merge_partial_minutes(partials, llm, doc_type) if len(partials) > 1 else partials[0]
    else:
        user = f"{meta_lines}{memo_block}\n### 스크립트:\n{script}"
        result = llm.chat(system, user, temp=0.3, model=MINUTES_MODEL, max_tokens=16000)
        usable, reason = _minutes_is_usable(result, len(script), doc_type)
        if not usable:
            warn(f"회의록 품질 미달({reason}) → 1회 재시도")
            retry_system = system + (
                "\n\n⚠️ 이전 시도가 너무 짧거나 필수 섹션이 빠졌습니다. "
                "위 출력 형식·길이 기준을 반드시 지켜 다시 작성하세요."
            )
            result = llm.chat(retry_system, user, temp=0.3, model=MINUTES_MODEL, max_tokens=16000)

    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "minutes_raw.md"), "Minutes raw")

    ok(f"{labels['title']} 생성 완료")
    return result


def refine_script(
    segments: List[Dict], llm: LLMClient,
    doc_type: str = "meeting",
    topic: str = "",
    debug_dir: Optional[str] = None,
) -> str:
    """STT 원문 스크립트를 전체 맥락과 주제를 참고하여 교정한 스크립트를 생성.
    오탈자·잘못 인식된 고유명사·전문용어를 수정하고 문장을 자연스럽게 다듬는다.
    """
    step("스크립트 교정 중...")

    use_ts = has_timestamps(segments)
    if use_ts:
        raw_script = "\n".join(
            f"[{ts(s['start'])}] {s.get('speaker', 'Speaker')}: {s['text']}"
            for s in segments
        )
    else:
        raw_script = "\n".join(
            f"{s.get('speaker', 'Speaker')}: {s['text']}"
            for s in segments
        )

    topic_line = f"주제: {topic}\n\n" if topic else ""
    type_hint = {"meeting": "회의", "seminar": "세미나/발표", "lecture": "강의"}.get(doc_type, "회의")

    system = (
        f"{topic_line}전문 {type_hint} 스크립트 교정 전문가입니다.\n"
        "STT(음성인식)로 생성된 원문 스크립트를 전체 맥락을 참고하여 교정하세요.\n\n"
        "교정 기준:\n"
        "- 잘못 인식된 고유명사, 인명, 제품명, 기술 용어를 맥락에 맞게 수정\n"
        "- 명백한 오탈자·음운 오류 수정 (예: '에이아이' → 'AI')\n"
        "- 문장이 어색하게 잘린 경우 자연스럽게 연결\n"
        "- 발화 습관(어, 음, 그, 뭐 등) 과도한 반복은 제거하되 발화 스타일은 유지\n"
        "- 타임스탬프·화자 레이블·전체 발화 순서는 절대 변경하지 말 것\n"
        "- 내용상 의미 변경 금지 — 교정이 불확실한 경우 원문 그대로 유지\n"
        "- 출력 형식은 입력과 동일하게 유지 (타임스탬프 있으면 그대로)"
    )
    user = f"다음 스크립트를 교정하세요:\n\n{raw_script}"

    if debug_dir:
        debug_save(user, os.path.join(debug_dir, "refine_prompt.txt"), "Refine prompt")

    result = llm.chat(system, user, temp=0.1)

    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "refined_script.txt"), "Refined script")

    ok("스크립트 교정 완료")
    return result


def _refined_script_is_usable(refined: Optional[str], segments: List[Dict]) -> Tuple[bool, str]:
    """LLM 교정본이 원문 대부분을 잃었는지 방어적으로 검증한다."""
    text = (refined or "").strip()
    if not text:
        return False, "교정 결과가 비어 있음"

    raw_text = "\n".join(str(s.get("text", "")) for s in segments or [])
    raw_len = len(raw_text.strip())
    refined_len = len(text)
    if raw_len >= 800 and refined_len < max(500, int(raw_len * 0.45)):
        return False, f"교정 결과가 원문 대비 과도하게 짧음 ({refined_len}/{raw_len}자)"

    if has_timestamps(segments):
        expected = len([s for s in segments if s.get("text")])
        found = len(re.findall(r"\[\d{1,2}:\d{2}(?::\d{2})?\]", text))
        if expected >= 10 and found < max(5, int(expected * 0.35)):
            return False, f"타임스탬프 보존율 부족 ({found}/{expected})"

    bad_tail_phrases = ("추가적인 질문", "언제든지 말씀", "도움이 되었기를")
    if raw_len >= 800 and any(p in text[-300:] for p in bad_tail_phrases) and refined_len < raw_len * 0.7:
        return False, "교정 결과가 답변형 요약으로 보임"

    return True, ""


_ACTIONS_SYSTEM_PROMPT = (
    "당신은 회의록 분석 전문가입니다.\n"
    "회의록에서 Action Item(다음 할 일, 후속 조치, 결정된 사항)을 "
    "추출해 JSON 배열로만 반환하세요.\n\n"
    "담당자(assignee) 규칙:\n"
    "- 실명 언급 시 → 해당 이름 그대로 사용\n"
    "- 실명 없어도 조직/역할이 명확하면 → 예: '고객사측', '주관사', '발표자', 언급된 회사명\n"
    "- 발화자 정보 없을 때도 문맥에서 추론: '우리가 다음 회의 전에 하기로 했어요' → 발화 측 조직\n"
    "- 어떤 조직/역할도 특정 불가능할 때만 → null (단, 실제로 결정된 사항이면 포함)\n\n"
    "기타 규칙:\n"
    "- 불확실한 제안이나 논의 중 사항은 제외, 합의/결정된 것만\n"
    "- deadline이 언급되지 않으면 null\n"
    "- 설명 없이 순수 JSON 배열만 출력 (코드블록 금지)\n\n"
    '출력 형식: [{"assignee":"담당자 또는 null","task":"업무 내용","deadline":"YYYY-MM-DD 또는 null","context":"맥락"}]'
)


def _extract_action_items_chunk(
    text: str, llm: LLMClient, debug_dir: Optional[str] = None, chunk_label: str = "",
) -> List[Dict]:
    """액션 아이템 추출 — 단일 청크(또는 전체 회의록)에 대해 LLM 1회 호출."""
    user = f"다음 회의록에서 Action Item을 추출하세요:\n\n{text}"
    if debug_dir:
        debug_save(user, os.path.join(debug_dir, f"actions_prompt{chunk_label}.txt"), "Actions prompt")
    raw = llm.chat(_ACTIONS_SYSTEM_PROMPT, user, temp=0.1)
    if debug_dir:
        debug_save(raw, os.path.join(debug_dir, f"actions_raw{chunk_label}.json"), "Actions raw")
    from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
    return parse_json_loose(raw, expect="list", default=[])


def _dedup_action_items(items: List[Dict]) -> List[Dict]:
    """(assignee, task) 정규화 키 기준 중복 제거 — 청크 오버랩 구간에서 같은
    액션이 여러 번 추출되는 것을 방지한다."""
    from meeting_minutes_app.wiki_core.wiki_knowledge import _norm_key

    seen = set()
    out: List[Dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        task_key = _norm_key(str(item.get("task") or ""))
        if not task_key:
            continue
        key = (_norm_key(str(item.get("assignee") or "")), task_key)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def extract_action_items(
    minutes: str, llm: LLMClient,
    doc_type: str = "meeting",
    debug_dir: Optional[str] = None,
) -> Optional[str]:
    """회의록에서 액션 아이템을 추출하여 JSON 문자열로 반환.
    meeting 타입만 지원. 항목이 없거나 추출 실패 시 None 반환.

    회의록이 발췌 한도(analysis.actions_source_max_chars)보다 길면 청크로 나눠
    각각 추출한 뒤 병합한다 — 긴 회의록 뒷부분의 액션이 통째로 누락되는 것을 방지.
    """
    if doc_type != "meeting":
        return None
    step("액션 아이템 추출 중...")

    try:
        from meeting_minutes_app.common import config_loader as _cfg
        _src_max = int(_cfg.get("analysis.actions_source_max_chars", 6000) or 6000)
    except Exception:
        _src_max = 6000

    if len(minutes) <= _src_max:
        items = _extract_action_items_chunk(minutes, llm, debug_dir)
    else:
        chunks = _split_script_chunks(minutes, _src_max)
        warn(f"회의록 {len(minutes):,}자 > {_src_max:,}자 → {len(chunks)}개 구간으로 나눠 액션 아이템 추출")
        items = []
        for idx, chunk in enumerate(chunks):
            items.extend(_extract_action_items_chunk(
                chunk, llm, debug_dir, chunk_label=f"_part{idx + 1}"))
        items = _dedup_action_items(items)

    if not items:
        ok("액션 아이템 없음")
        return None

    ok(f"액션 아이템 {len(items)}개 추출")
    return json.dumps(items, ensure_ascii=False, indent=2)


def format_actions_md(actions_json: str) -> str:
    """JSON 액션 아이템을 마크다운 테이블로 변환."""
    try:
        items = json.loads(actions_json)
    except Exception:
        return actions_json
    if not items:
        return "*(액션 아이템 없음)*"
    lines = [
        "# 액션 아이템\n",
        "| 담당자 | 업무 | 마감일 | 맥락 |",
        "| --- | --- | --- | --- |",
    ]
    for item in items:
        lines.append(
            f"| {item.get('assignee') or '-'} "
            f"| {item.get('task') or '-'} "
            f"| {item.get('deadline') or '-'} "
            f"| {item.get('context') or '-'} |"
        )
    return "\n".join(lines)


def generate_summary(
    minutes: str, llm: LLMClient,
    doc_type: str = "meeting",
    debug_dir: Optional[str] = None,
    topic: str = "",
    session_dt: str = "",
) -> str:
    labels = TYPE_LABELS[doc_type]
    step("요약본 생성 중...")

    system = _get_summary_prompt(doc_type, topic, session_dt)
    meta_lines = ""
    if session_dt: meta_lines += f"일시: {session_dt}\n"
    if topic:      meta_lines += f"주제: {topic}\n"
    result = llm.chat(system,
                      f"{meta_lines}다음 {labels['title']}을 요약하세요:\n\n{minutes}",
                      temp=0.2, model=SUMMARY_MODEL, max_tokens=8000)
    if debug_dir:
        debug_save(result, os.path.join(debug_dir, "summary_raw.md"), "Summary raw")

    ok("요약본 생성 완료")
    return result


# ──────────────────────────────────────────────
#  파일 저장
# ──────────────────────────────────────────────
def save(content: str, path: str, label: str):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    ok(f"{label} → {path}")


# ──────────────────────────────────────────────
#  화자 이름 LLM 추론
# ──────────────────────────────────────────────
def infer_speaker_names(
    segments: List[Dict],
    llm: LLMClient,
    known_names: Optional[List[str]] = None,
) -> Dict[str, str]:
    """diarize 모델이 반환한 'Speaker A/B/C' 레이블을 발화 패턴으로 실명·역할 추론.

    Returns:
        {"Speaker A": "추론된 이름/역할", ...} — 추론 불가 시 빈 dict
    """
    unique_speakers = list({s.get("speaker", "") for s in segments if s.get("speaker")})
    if not unique_speakers:
        return {}
    # 알려진 참석자 명단이 없으면 추론하지 않음 — 화자 레이블(화자1/2…) 그대로 유지(지어내기 방지)
    if not known_names:
        return {}

    # 각 화자별 대표 발언 최대 5개 샘플링
    samples: Dict[str, List[str]] = {}
    for spk in unique_speakers:
        spk_texts = [s["text"] for s in segments if s.get("speaker") == spk][:5]
        if spk_texts:
            samples[spk] = spk_texts

    if not samples:
        return {}

    system = (
        "회의 발화 분석가입니다. 각 화자 레이블이 '알려진 참석자 명단' 중 누구인지만 판단하세요.\n"
        "규칙:\n"
        "- 자기소개·명시적 호명 등으로 명단 속 인물과 **확실히** 일치할 때만 그 실명으로 매핑.\n"
        "- 불확실하거나 명단에 없으면 해당 키를 **출력에서 생략**(화자 레이블 그대로 유지).\n"
        "- 이름·역할·직책·소속을 **추측하거나 지어내지 말 것.** 명단에 없는 새 이름 생성 절대 금지.\n"
        '출력: {"Speaker A": "명단속이름", ...} 형식의 순수 JSON만. 설명 금지.'
    )
    known_hint = f"\n알려진 참석자 명단(이 안의 이름만 사용): {', '.join(known_names)}"
    user = json.dumps(samples, ensure_ascii=False) + known_hint

    try:
        raw = llm.chat(system, user, temp=0.1)
        from meeting_minutes_app.meeting_pipeline.json_utils import parse_json_loose
        mapping = parse_json_loose(raw, expect="dict", default={})
        if not mapping:
            return {}
        kn = {n.strip() for n in known_names}
        # 명단에 있는 이름으로 매핑된 것만 인정(그 외는 화자 레이블 유지 → 지어내기 차단)
        return {k: v.strip() for k, v in mapping.items()
                if v and isinstance(v, str) and v.strip() in kn}
    except Exception as e:
        logger.debug(f"[infer_speaker_names] 실패: {e}")
        return {}

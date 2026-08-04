/**
 * usage_log.kind → 한국어 라벨 (단일 소스).
 *
 * kind 값의 정본은 `meeting_minutes_app/common/spend_guard.py` 다
 * (KIND_WATCHER / KIND_PLAN_AUTOMATION / KIND_REGENERATE / KIND_FACILITATION /
 *  KIND_WEB_RESEARCH + 위키 임베딩의 "embedding").
 *
 * 이 표가 없어서 한국어 화면에 'facilitation' 같은 영어 키가 그대로 노출된 적이 있고,
 * 그 뒤 비용 요약(CostSummary)과 회의 상세(SessionDetail)가 **각자** 표를 갖게 되면서
 * 한쪽에만 kind 가 추가되는 갈라짐이 시작됐다. 이 리포가 반복해서 없애온 형태라
 * (단가 표 4곳·노트 판정 2곳) 표는 여기 하나만 둔다.
 *
 * 새 kind 를 추가하면 여기 한 줄 추가한다. 빠져도 화면은 깨지지 않고 raw key 로
 * 폴백한다(kindLabel).
 */
export const KIND_LABELS: Record<string, string> = {
  embedding: "위키 임베딩(검색 인덱스)",
  watcher: "폴더 자동 감시",
  plan_automation: "계획 자동화",
  regenerate: "회의록 재생성",
  // 아래 둘은 회의 '중'에 쓰는 돈이지만 세션 비용(sessions.cost_estimate)에는 넣지
  // 않는다 — usage_log 와 이중 집계되기 때문이다(month_to_date_spend 가 둘을 더한다).
  // 대신 note 에 세션 키를 남겨 회의 상세에서 **실측값**으로 되찾아 보여준다.
  facilitation: "회의 진행 페르소나(회의 중 트리아지)",
  web_research: "회의 중 웹 검색 보완",
};

export const kindLabel = (kind: string): string => KIND_LABELS[kind] ?? kind;

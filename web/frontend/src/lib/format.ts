/** 공통 포맷 유틸리티 */

export function formatDuration(s: number): string {
  if (!s) return "";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return m > 0 ? `${m}m ${sec}s` : `${sec}s`;
}

export function formatTime(sec: number): string {
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m.toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
}

/**
 * 시계 표기 `[HH:]MM:SS` — 녹음 타이머·재생 위치처럼 **자릿수가 흔들리면 안 되는** 자리.
 *
 * `formatDuration` 과 굳이 나눠 둔 이유: 그쪽은 "3m 20s" 처럼 읽는 표기라 폭이 계속 바뀐다.
 * 종전에는 Recorder 가 같은 이름(`formatDuration`)의 지역 함수로 이 형식을 따로 갖고 있어서,
 * 한 리포에 이름은 같고 결과가 다른 함수가 둘이었다.
 */
export function formatClock(sec: number): string {
  const total = Math.max(0, Math.floor(sec || 0));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = m.toString().padStart(2, "0");
  const ss = s.toString().padStart(2, "0");
  return h > 0 ? `${h.toString().padStart(2, "0")}:${mm}:${ss}` : `${mm}:${ss}`;
}

export function formatDate(d: string): string {
  if (!d) return "";
  // `new Date("아무말")` 은 **던지지 않는다** — Invalid Date 를 만들고
  // toLocaleDateString 이 문자열 "Invalid Date" 를 돌려준다. 그래서 아래 catch 만
  // 두었을 때는 폴백이 한 번도 동작하지 않고 화면에 "Invalid Date" 가 그대로 나왔다
  // (프런트 테스트를 붙이면서 발견). 유효성은 getTime() 으로 판정해야 한다.
  try {
    const dt = new Date(d);
    if (Number.isNaN(dt.getTime())) return d;
    return dt.toLocaleDateString("ko-KR", {
      month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch { return d; }
}

/** 문서유형 한국어 라벨. UI 기본 언어가 한국어인데 화면에 따라 raw 값(meeting/
 *  seminar/lecture)이 그대로 노출되던 것을 한 곳으로 모았다. */
export function typeLabel(t: string): string {
  return ({ meeting: "회의", seminar: "세미나", lecture: "강의",
            prep: "회의 준비" } as Record<string, string>)[t] || t || "기타";
}

/** 처리 상태 한국어 라벨. 모르는 값은 그대로 보여준다(서버가 새 상태를 추가해도
 *  화면이 비지 않게). */
export function statusLabel(s: string): string {
  return ({ completed: "완료", processing: "처리 중", error: "오류",
            pending: "대기 중" } as Record<string, string>)[s] || s;
}

/**
 * 문서유형 배지의 색 — 디자인 토큰만 쓴다(원시 팔레트 금지, PRD §5.1).
 *
 * 상태색(rec/proc/ok/idle)과 **다른 축**이라 유형에는 액센트·상태 hue 를 쓰지 않는다.
 * 유형은 이미 글자로 구분되므로(회의/세미나/강의) 색은 보조 신호일 뿐 — 엔티티 팔레트를
 * 빌려 은은하게만 구분한다. 모르는 유형도 반드시 비지 않은 클래스를 돌려준다(배지가 깨진다).
 */
export function typeColor(t: string): string {
  switch (t) {
    case "meeting": return "bg-surface-2 text-ent-meeting border-line-strong";
    case "seminar": return "bg-surface-2 text-ent-topic border-line-strong";
    case "lecture": return "bg-surface-2 text-ent-person border-line-strong";
    case "prep": return "bg-surface-2 text-ent-project border-line-strong";
    default: return "bg-surface-2 text-ink-2 border-line-strong";
  }
}

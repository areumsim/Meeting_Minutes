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

export function formatDate(d: string): string {
  if (!d) return "";
  try {
    return new Date(d).toLocaleDateString("ko-KR", {
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

export function typeColor(t: string): string {
  switch (t) {
    case "meeting": return "bg-blue-100 text-blue-700";
    case "seminar": return "bg-purple-100 text-purple-700";
    case "lecture": return "bg-amber-100 text-amber-700";
    default: return "bg-zinc-100 text-zinc-700";
  }
}

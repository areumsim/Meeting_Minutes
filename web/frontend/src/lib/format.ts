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

export function typeColor(t: string): string {
  switch (t) {
    case "meeting": return "bg-blue-100 text-blue-700";
    case "seminar": return "bg-purple-100 text-purple-700";
    case "lecture": return "bg-amber-100 text-amber-700";
    default: return "bg-zinc-100 text-zinc-700";
  }
}

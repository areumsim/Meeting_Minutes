import { useEffect, useState } from "react";

/**
 * 미디어쿼리 구독.
 *
 * CSS 로 `hidden md:table` / `md:hidden` 을 겹쳐 두면 **두 트리가 모두 DOM 에 들어간다** —
 * 표 50행이면 카드 50장이 함께 만들어져 노드가 두 배가 되고, 라이브러리처럼 행이 계속
 * 쌓이는 화면에서는 그대로 첫 렌더 비용이 된다(PRD §11 "고밀도·성능 불변"). 그래서
 * 레이아웃이 통째로 갈리는 곳은 CSS 가 아니라 여기서 **하나만** 고른다.
 *
 * 잔글씨 수준의 반응형(패딩·글자 크기)은 계속 CSS 로 한다 — 이 훅은 트리가 달라질 때만.
 *
 * matchMedia 가 없는 환경(구형 WebView·일부 테스트 러너)에서는 `fallback` 을 쓴다.
 * 기본값을 데스크톱으로 두는 이유: 이 앱의 1차 대상이 Windows PC 이고, 잘못 골랐을 때
 * 카드 리스트보다 표 쪽이 정보 손실이 없다.
 */
export function useMediaQuery(query: string, fallback = true): boolean {
  const [matches, setMatches] = useState(() => {
    try { return window.matchMedia?.(query).matches ?? fallback; }
    catch { return fallback; }
  });

  useEffect(() => {
    let mq: MediaQueryList;
    try { mq = window.matchMedia(query); } catch { return; }
    const onChange = () => setMatches(mq.matches);
    onChange();
    mq.addEventListener?.("change", onChange);
    return () => mq.removeEventListener?.("change", onChange);
  }, [query]);

  return matches;
}

/** Tailwind 의 `md` 중단점과 같은 값. 두 곳이 갈라지면 표와 카드가 동시에 사라진다. */
export const MD_UP = "(min-width: 768px)";

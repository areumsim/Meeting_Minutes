import type { KeyboardEvent } from "react";

/**
 * Enter 로 실행, Shift+Enter 로 줄바꿈 — **한글 조합 가드 포함**.
 *
 * `isComposing` 을 안 보면 한글 후보를 확정하려는 Enter 가 그대로 전송이 된다. 위키 질문과
 * 회의 준비 브리핑 둘 다 그 Enter 로 **LLM 호출(비용)** 이 나가고, 전송 뒤 입력창을 비우므로
 * 쓰던 문장까지 사라졌다. 같은 가드가 두 화면에 따로 적혀 있었어서 한 곳으로 모은다.
 */
export function submitOnEnter<T extends HTMLElement>(
  run: () => void,
  opts: { allowShiftNewline?: boolean } = {},
) {
  const { allowShiftNewline = true } = opts;
  return (e: KeyboardEvent<T>) => {
    if (e.key !== "Enter") return;
    if (allowShiftNewline && e.shiftKey) return;
    // nativeEvent 가 없는 합성 이벤트(일부 테스트 유틸)에서도 죽지 않게 방어한다.
    if ((e.nativeEvent as unknown as { isComposing?: boolean })?.isComposing) return;
    e.preventDefault();
    run();
  };
}

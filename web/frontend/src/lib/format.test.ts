import { describe, expect, it } from "vitest";
import {
  formatDuration, formatTime, formatDate, typeLabel, statusLabel, typeColor,
} from "./format";

/**
 * 표시 포맷은 화면 전체가 공유하는 단일 소스다(`format.ts` 주석: 화면마다 raw 값이
 * 노출되던 것을 한 곳으로 모았다). 여기서 고정하는 것은 **모르는 값이 들어와도 화면이
 * 비지 않는다**는 계약이다 — 서버가 새 상태·유형을 추가했을 때 대시보드가 공백이 되면
 * 사용자는 원인을 알 수 없다.
 */
describe("formatDuration", () => {
  it("분이 있으면 분과 초를 함께 보인다", () => {
    expect(formatDuration(125)).toBe("2m 5s");
  });

  it("1분 미만은 초만 보인다", () => {
    expect(formatDuration(42)).toBe("42s");
  });

  it("0 은 빈 문자열 — 화면에서 '0s' 를 숨기기 위한 의도된 동작", () => {
    expect(formatDuration(0)).toBe("");
  });

  it("소수 초를 버린다", () => {
    expect(formatDuration(59.9)).toBe("59s");
  });
});

describe("formatTime", () => {
  it("mm:ss 로 0 을 채운다", () => {
    expect(formatTime(0)).toBe("00:00");
    expect(formatTime(65)).toBe("01:05");
  });

  it("60분을 넘겨도 분이 계속 늘어난다(시:분 으로 바뀌지 않는다)", () => {
    expect(formatTime(3725)).toBe("62:05");
  });
});

describe("formatDate", () => {
  it("빈 값은 빈 문자열", () => {
    expect(formatDate("")).toBe("");
  });

  it("[회귀] 파싱 불가한 값은 원문을 그대로 — 'Invalid Date' 가 화면에 나오면 안 된다", () => {
    // 원래 코드는 `catch { return d }` 로 방어했지만 `new Date("아무말")` 은 던지지 않고
    // toLocaleDateString 이 문자열 "Invalid Date" 를 돌려줘서 폴백이 한 번도 동작하지
    // 않았다. 이 테스트를 쓰면서 발견했다.
    expect(formatDate("nonsense")).toBe("nonsense");
    expect(formatDate("2026-13-45")).not.toContain("Invalid");
  });

  it("ISO 문자열을 한국어 로케일로 보인다", () => {
    const out = formatDate("2026-08-03T14:05:00");
    expect(out).toMatch(/8/);       // 월이 들어간다(정확한 형식은 로케일 구현에 맡긴다)
    expect(out).not.toBe("");
  });
});

describe("typeLabel / statusLabel — 모르는 값 처리", () => {
    it("알려진 유형을 한국어로 바꾼다", () => {
    expect(typeLabel("meeting")).toBe("회의");
    expect(typeLabel("seminar")).toBe("세미나");
  });

  it("모르는 유형은 **원문을 그대로**, 빈 값만 '기타'", () => {
    // statusLabel 과 같은 방침이다 — 서버가 새 유형을 추가해도 배지가 비지 않는다.
    expect(typeLabel("workshop")).toBe("workshop");
    expect(typeLabel("")).toBe("기타");
  });

  it("알려진 상태를 한국어로 바꾼다", () => {
    expect(statusLabel("completed")).toBe("완료");
    expect(statusLabel("processing")).toBe("처리 중");
    expect(statusLabel("error")).toBe("오류");
  });

  it("모르는 상태는 **원문을 그대로** 보인다 — 서버가 새 상태를 추가해도 빈칸이 되지 않게", () => {
    expect(statusLabel("queued")).toBe("queued");
  });
});

describe("typeColor", () => {
  it("유형별로 다른 색을 준다", () => {
    const colors = ["meeting", "seminar", "lecture"].map(typeColor);
    expect(new Set(colors).size).toBe(3);
  });

  it("모르는 유형에도 색을 준다(클래스가 비면 배지가 깨진다)", () => {
    expect(typeColor("workshop")).toContain("bg-");
  });
});

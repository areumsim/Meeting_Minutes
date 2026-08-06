import { describe, expect, it } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";

/**
 * 색 클래스가 **실제로 존재하는 토큰**을 가리키는지 검사한다.
 *
 * 왜 필요한가 — 재설계에서 원시 팔레트 522곳을 토큰으로 옮길 때 문자열 치환을 썼는데,
 * 짧은 이름이 긴 이름의 접두사라 조용히 깨진 클래스가 생겼다:
 *   `bg-emerald-500` 안의 `bg-emerald-50` 이 치환돼 **`bg-ok-bg0`** 이 됐다.
 * Tailwind 는 모르는 클래스를 그냥 무시하므로 빌드도 타입 검사도 통과하고, 화면에서는
 * 그 요소만 **색 없이** 렌더된다(실제로 폴더 감시의 상태 점 3개와 온보딩 진행바가
 * 보이지 않았다. 눈으로 보기 전까지 아무도 몰랐다).
 *
 * 이 테스트는 index.css 의 `--color-*` 목록을 정본으로 삼아, 소스의 모든 색 유틸리티가
 * 그 안에 있는지 본다. 새 색이 필요하면 **토큰을 먼저 만들라**는 규칙의 집행자다.
 */

const SRC = join(__dirname, "..");
const CSS = join(SRC, "index.css");

/** Tailwind 가 기본 제공하는 색 키워드 — 토큰이 아니어도 유효하다. */
const BUILTIN = new Set(["white", "black", "transparent", "current", "inherit", "none"]);

/** 색이 아닌 동명 유틸리티(text-sm, border-dashed …)는 검사 대상이 아니다. */
const NON_COLOR: Record<string, RegExp> = {
  text: /^(xs|sm|base|md|lg|xl|\d?xl|left|right|center|justify|start|end|ellipsis|clip|nowrap|wrap|balance|pretty)$/,
  border: /^(dashed|solid|dotted|double|none|hidden|collapse|separate|spacing|[trblxyse])$/,
  bg: /^(clip|cover|contain|center|fixed|local|scroll|repeat|no|none|gradient|origin|blend|auto|top|bottom|left|right)$/,
  ring: /^(inset|offset)$/,
  fill: /^(none)$/,
  stroke: /^(none)$/,
  decoration: /^(dashed|dotted|solid|double|wavy|none|from|auto)$/,
  accent: /^(auto)$/,
  from: /^\d+$/,
  to: /^\d+$/,
};

function listFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) return listFiles(p);
    return /\.tsx?$/.test(name) ? [p] : [];
  });
}

function tokenNames(): Set<string> {
  const css = readFileSync(CSS, "utf-8");
  return new Set([...css.matchAll(/--color-([a-z0-9-]+):/g)].map((m) => m[1]));
}

/**
 * `className` 값만 뽑는다. 파일 전체를 훑으면 `id="text-body"` 같은 문자열이 색 클래스로
 * 오인된다 — 검사가 거짓 경보를 내면 곧 꺼지고, 꺼진 검사는 없는 것과 같다.
 * 문자열/템플릿 리터럴 안만 보고, 중괄호는 단순히 짝을 세어 넘어간다(이 리포의 className 은
 * 문자열 결합과 삼항뿐이라 이 정도로 충분하다).
 */
function classNameRegions(src: string): string[] {
  const out: string[] = [];
  const re = /className=(\{|")/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(src))) {
    const start = m.index + m[0].length;
    if (m[1] === '"') {
      const end = src.indexOf('"', start);
      if (end > start) out.push(src.slice(start, end));
      continue;
    }
    let depth = 1;
    let i = start;
    while (i < src.length && depth > 0) {
      if (src[i] === "{") depth++;
      else if (src[i] === "}") depth--;
      i++;
    }
    out.push(src.slice(start, i - 1));
  }
  return out;
}

/** className 문자열에서 알 수 없는 색 클래스를 뽑는다. */
export function unknownColorClasses(src: string, tokens: Set<string>): string[] {
  const out: string[] = [];
  const re =
    /(?<![\w-])(?:(?:hover|focus|focus-visible|active|disabled|group-hover|group-focus-within|dark|sm|md|lg|xl|motion-reduce|last|first|odd|even):)*(bg|text|border|ring|fill|stroke|decoration|accent|from|to)-([trblxyse]-)?([a-z][a-z0-9-]*)(?:\/\d+)?(?![\w-])/g;
  for (const region of classNameRegions(src)) {
    for (const m of region.matchAll(re)) {
      const util = m[1];
      const name = m[3];
      // 폭·간격 유틸리티(border-b-2, border-spacing-0 …)는 색이 아니다.
      if (/^(?:[trblxyse]-)?\d+$/.test(name) || /^spacing-\d+$/.test(name)) continue;
      if (BUILTIN.has(name) || tokens.has(name)) continue;
      if (NON_COLOR[util]?.test(name)) continue;
      out.push(m[0]);
    }
  }
  return [...new Set(out)];
}

describe("색 클래스는 정의된 토큰만 가리킨다", () => {
  const tokens = tokenNames();

  it("index.css 가 토큰을 실제로 정의한다(정본이 비면 검사가 무의미하다)", () => {
    expect(tokens.size).toBeGreaterThan(20);
    for (const must of ["ink", "ink-2", "ink-3", "surface", "line", "accent", "rec", "ok", "proc", "warn"]) {
      expect(tokens.has(must), `토큰 --color-${must} 가 없다`).toBe(true);
    }
  });

  it("소스에 정의되지 않은 색 클래스가 없다", () => {
    const offenders: string[] = [];
    for (const file of listFiles(SRC)) {
      if (/\.test\.tsx?$/.test(file)) continue;   // 테스트는 문자열 픽스처를 담는다
      const bad = unknownColorClasses(readFileSync(file, "utf-8"), tokens);
      if (bad.length) offenders.push(`${relative(SRC, file)}: ${bad.join(", ")}`);
    }
    expect(offenders, `\n${offenders.join("\n")}\n`).toEqual([]);
  });

  it("[회귀] 접두사 치환이 만든 형태를 잡는다", () => {
    // bg-emerald-500 → (bg-emerald-50 치환) → bg-ok-bg0. Tailwind 는 조용히 무시한다.
    expect(unknownColorClasses('<div className="bg-ok-bg0" />', tokens)).toEqual(["bg-ok-bg0"]);
    expect(unknownColorClasses('<div className="bg-surface-20" />', tokens)).toEqual(["bg-surface-20"]);
    // 정상 토큰과 폭 유틸리티는 통과한다.
    expect(unknownColorClasses(
      '<div className="bg-ok-bg text-ink-3 border-l-rec border-b-2 last:border-b-0 border-spacing-0" />',
      tokens)).toEqual([]);
    // className 이 아닌 문자열(id·htmlFor)은 보지 않는다 — 거짓 경보가 나면 검사가 꺼진다.
    expect(unknownColorClasses('<label htmlFor="text-body" />', tokens)).toEqual([]);
  });
});

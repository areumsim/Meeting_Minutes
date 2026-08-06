import React from "react";
import ReactMarkdown, { defaultUrlTransform } from "react-markdown";
import remarkGfm from "remark-gfm";

// 공용 마크다운 렌더러.
//  - remark-gfm: 표(| a | b |)·체크박스(- [ ])·취소선 등 GitHub 확장 문법 렌더
//  - HTML 주석(<!-- Generated ... -->) 등 provenance 헤더는 화면에서 숨김(파일엔 유지)
//  - Tailwind Typography(prose)로 서식. 표는 가로 스크롤 래핑.
//  - onWikiLink 가 주어지면 본문의 [[위키링크]]를 클릭 가능한 링크로 렌더(지식 그래프로 이동).
//    주어지지 않으면 원문 그대로 두어 기존 동작을 100% 보존한다.

const WIKILINK_RE = /\[\[([^\]]+)\]\]/g;

// [[대상]] / [[대상|별칭]] / [[대상#헤딩]] → 마크다운 링크 [별칭](#wiki:인코딩)
// 해시(#) 프리픽스라 react-markdown URL 새니타이저에 프로토콜로 오인되지 않는다.
function injectWikiLinks(md: string): string {
  return md.replace(WIKILINK_RE, (_m, inner: string) => {
    const raw = String(inner);
    const target = raw.split("|")[0].split("#")[0].trim();
    if (!target) return _m;                 // 빈 링크는 원문 유지
    const alias = raw.includes("|") ? raw.split("|")[1].trim() : target;
    const display = (alias || target).replace(/\]/g, "");   // 링크 텍스트 안전화
    return `[${display}](#wiki:${encodeURIComponent(target)})`;
  });
}

export default function Markdown({
  content,
  className = "",
  onWikiLink,
}: {
  content: string;
  className?: string;
  onWikiLink?: (target: string) => void;
}) {
  let clean = (content || "").replace(/<!--[\s\S]*?-->/g, "").trim();
  if (onWikiLink) clean = injectWikiLinks(clean);

  return (
    <div className={`prose prose-zinc max-w-none prose-table:my-3 prose-th:text-left ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        // 우리가 만든 #wiki: 링크만 통과시키고 나머지는 기본 새니타이즈 유지(보안).
        urlTransform={(url) => (url.startsWith("#wiki:") ? url : defaultUrlTransform(url))}
        components={{
          // 표가 좁은 화면을 밀어내지 않도록 가로 스크롤 컨테이너로 감싼다.
          table: ({ node, ...props }) => (
            <div style={{ overflowX: "auto" }}>
              <table {...props} />
            </div>
          ),
          a: ({ node, href, children, ...props }) => {
            if (onWikiLink && typeof href === "string" && href.startsWith("#wiki:")) {
              const target = decodeURIComponent(href.slice("#wiki:".length));
              return (
                <a
                  href={href}
                  onClick={(e) => {
                    e.preventDefault();
                    onWikiLink(target);
                  }}
                  className="text-ink-2 font-medium no-underline border-b border-dashed border-line-strong hover:border-ink-2 hover:text-ink cursor-pointer"
                  title={`지식 그래프에서 '${target}' 열기`}
                >
                  {children}
                </a>
              );
            }
            // 일반 링크는 새 탭 + 안전 rel.
            return (
              <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
                {children}
              </a>
            );
          },
        }}
      >
        {clean}
      </ReactMarkdown>
    </div>
  );
}

import React from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// 공용 마크다운 렌더러.
//  - remark-gfm: 표(| a | b |)·체크박스(- [ ])·취소선 등 GitHub 확장 문법 렌더
//  - HTML 주석(<!-- Generated ... -->) 등 provenance 헤더는 화면에서 숨김(파일엔 유지)
//  - Tailwind Typography(prose)로 서식. 표는 가로 스크롤 래핑.
export default function Markdown({ content, className = "" }: { content: string; className?: string }) {
  const clean = (content || "").replace(/<!--[\s\S]*?-->/g, "").trim();
  return (
    <div className={`prose prose-zinc max-w-none prose-table:my-3 prose-th:text-left ${className}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          // 표가 좁은 화면을 밀어내지 않도록 가로 스크롤 컨테이너로 감싼다.
          table: ({ node, ...props }) => (
            <div style={{ overflowX: "auto" }}>
              <table {...props} />
            </div>
          ),
        }}
      >
        {clean}
      </ReactMarkdown>
    </div>
  );
}

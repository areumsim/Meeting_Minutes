import React, { useState, useEffect } from "react";
import { Search, Network, ArrowLeft, X } from "lucide-react";
import { listGraphNodes, getNodeNeighbors } from "../../lib/api";
import { entityLabel } from "../../lib/entities";
import { Button, IconButton } from "../../ui/Button";
import { Input } from "../../ui/Field";
import { Banner } from "../../ui/Banner";
import GraphView, { GraphNodeList } from "../../ui/GraphView";
import { Spinner, EmptyState } from "../../ui/states";
import { submitOnEnter } from "../../lib/useSubmitOnEnter";
import type { GraphNode, GraphEdge, GraphNeighbors } from "../../lib/types";

/**
 * 지식 그래프 탐색 — PRD FR-KNO-3.
 *
 * 렌더러와 팔레트는 회의 상세의 그래프 탭과 **공유한다**(ui/GraphView + lib/entities).
 * 노드 칩 목록(GraphNodeList)이 키보드 경로이자 시각 목록이다 — SVG 만 두면 포인터로만
 * 쓸 수 있고, 숨긴 접근성 경로는 아무도 눈으로 확인하지 않아 곧 썩는다.
 */
export default function GraphPanel({ initialQuery = "" }: { initialQuery?: string }) {
  const [q, setQ] = useState(initialQuery);
  const [results, setResults] = useState<GraphNode[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");

  // 드릴다운 스택(뒤로가기용)
  const [stack, setStack] = useState<GraphNode[]>([]);
  const [focus, setFocus] = useState<GraphNeighbors | null>(null);
  const [loadingNode, setLoadingNode] = useState(false);

  // 검색 결과 "연결 개요" — 들어가 보지 않아도 결과가 어떻게 이어져 있는지 바로 보여준다.
  const [overview, setOverview] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);

  /** 결과 상위 노드들의 1-hop 이웃/엣지를 하나의 그래프로 합친다(중복 제거). */
  const loadOverview = async (res: GraphNode[]) => {
    if (!res.length) { setOverview(null); return; }
    setOverviewLoading(true);
    try {
      const seeds = res.slice(0, 7);   // 요청 수 제한(로컬 백엔드 부담·가독성)
      const parts = await Promise.all(
        seeds.map((n) => getNodeNeighbors(n.id, { depth: 1, limit: 24 }).catch(() => null)),
      );
      const nodeMap = new Map<string, GraphNode>();
      const edgeMap = new Map<string, GraphEdge>();
      for (const n of res) nodeMap.set(n.id, n);   // 결과 노드는 이웃이 없어도 항상 포함
      for (const p of parts) {
        if (!p) continue;
        if (p.node) nodeMap.set(p.node.id, p.node);
        for (const nb of p.neighbors) nodeMap.set(nb.id, nb);
        for (const e of p.edges) edgeMap.set(e.id, e);
      }
      setOverview({ nodes: [...nodeMap.values()], edges: [...edgeMap.values()] });
    } catch {
      setOverview(null);
    }
    setOverviewLoading(false);
  };

  const openNode = async (node: GraphNode, pushStack = true) => {
    setLoadingNode(true); setError("");
    try {
      const n = await getNodeNeighbors(node.id, { depth: 1, limit: 100 });
      setFocus(n);
      if (pushStack) setStack((s) => [...s, node]);
    } catch (e: any) {
      setError(e?.message || "노드를 불러오지 못했습니다.");
    }
    setLoadingNode(false);
  };

  const runSearch = async () => {
    setSearching(true); setError(""); setSearched(true);
    setFocus(null); setStack([]);   // 새 검색 시 상세에서 결과 개요로 복귀
    try {
      const nodes = await listGraphNodes({ q: q.trim() || undefined, limit: 50 });
      setResults(nodes);
      void loadOverview(nodes);
    } catch (e: any) {
      setError(e?.message || "검색에 실패했습니다.");
      setResults([]); setOverview(null);
    }
    setSearching(false);
  };

  // 진입 시: initialQuery(위키링크 클릭 등)가 있으면 그 대상을 검색해 정확히 일치하는
  // 노드를 자동으로 펼친다. 없으면 상위 노드 목록부터.
  useEffect(() => {
    const term = (initialQuery || "").trim();
    setQ(term);
    if (!term) { runSearch(); return; }
    (async () => {
      setSearching(true); setError(""); setSearched(true);
      try {
        const nodes = await listGraphNodes({ q: term, limit: 50 });
        setResults(nodes);
        const exact = nodes.find((n) => n.label.toLowerCase() === term.toLowerCase());
        const target = exact || nodes[0];
        if (target) await openNode(target);
      } catch (e: any) {
        setError(e?.message || "검색에 실패했습니다.");
        setResults([]);
      }
      setSearching(false);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  const goBack = async () => {
    const s = [...stack];
    s.pop();
    const prev = s[s.length - 1];
    setStack(s);
    if (prev) await openNode(prev, false);
    else setFocus(null);
  };

  // 이웃을 관계 종류별로 묶는다.
  const grouped: Record<string, GraphNode[]> = {};
  if (focus?.node) {
    const byId = new Map(focus.neighbors.map((n) => [n.id, n]));
    for (const e of focus.edges) {
      const otherId = e.from_node_id === focus.node.id ? e.to_node_id : e.from_node_id;
      const other = byId.get(otherId);
      if (other) (grouped[e.relation_type] ||= []).push(other);
    }
  }

  return (
    <div className="mx-auto max-w-4xl space-y-3">
      <div className="flex gap-2">
        <div className="relative flex-1">
          <Search size={14} aria-hidden="true"
            className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-ink-3" />
          <Input aria-label="지식 그래프 노드 검색" value={q} className="pl-8"
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={submitOnEnter(runSearch, { allowShiftNewline: false })}
            placeholder="이름·키워드로 찾기 (비우면 상위 노드)" />
        </div>
        <Button variant="primary" icon={Search} busy={searching} onClick={runSearch}>검색</Button>
      </div>

      {error && (
        <Banner title="그래프를 불러오지 못했습니다" onDismiss={() => setError("")}>
          {error} — 지식 그래프가 꺼져 있거나 아직 데이터가 없을 수 있습니다.
          [설정] → 노트 폴더를 지정하고 [검색 인덱스·그래프 재빌드]를 눌러 보세요.
        </Banner>
      )}

      {focus?.node ? (
        <section className="rounded-card border border-line bg-surface p-3 shadow-card">
          <div className="mb-2 flex items-center gap-2">
            {stack.length > 1 && (
              <Button size="sm" variant="ghost" icon={ArrowLeft} onClick={goBack}>뒤로</Button>
            )}
            <span className="flex items-baseline gap-1.5">
              <span className="text-xs text-ink-3">{entityLabel(focus.node.type)}</span>
              <h3 className="text-md font-bold text-ink">{focus.node.label}</h3>
            </span>
            <div className="flex-1" />
            <IconButton icon={X} size="sm" label="닫기"
              onClick={() => { setStack([]); setFocus(null); }} />
          </div>

          {loadingNode && <Spinner label="연결을 불러오는 중" size={14} />}

          {focus.node.attributes && Object.keys(focus.node.attributes).length > 0 && (
            <dl className="mb-2 space-y-0.5 text-xs text-ink-3">
              {Object.entries(focus.node.attributes).slice(0, 8).map(([k, v]) => (
                <div key={k} className="flex gap-1.5">
                  <dt className="font-semibold">{k}</dt>
                  <dd className="truncate">{String(v)}</dd>
                </div>
              ))}
            </dl>
          )}

          {focus.neighbors.length > 0 && (
            <div className="mb-3 rounded-card border border-line bg-surface-2/50">
              <GraphView nodes={[focus.node, ...focus.neighbors]} edges={focus.edges}
                centerId={focus.node.id} activeId={focus.node.id}
                onNodeClick={(n) => { if (n.id !== focus.node?.id) openNode(n); }} />
            </div>
          )}

          {Object.keys(grouped).length > 0 ? (
            <div className="space-y-2.5">
              {Object.entries(grouped).map(([rel, nodes]) => (
                <div key={rel}>
                  <h4 className="mb-1 text-xs font-semibold text-ink-3">{rel} ({nodes.length})</h4>
                  <GraphNodeList nodes={nodes} onSelect={openNode} />
                </div>
              ))}
            </div>
          ) : (
            !loadingNode && <p className="text-sm text-ink-3">연결된 이웃이 없습니다.</p>
          )}
        </section>
      ) : searching ? (
        <Spinner label="그래프를 불러오는 중" />
      ) : results.length > 0 ? (
        <>
          {overview && overview.nodes.length > 0 && (
            <section className="overflow-hidden rounded-card border border-line bg-surface shadow-card">
              <h3 className="flex items-center gap-1.5 px-3 pt-2 text-xs font-semibold text-ink-3">
                <Network size={12} aria-hidden="true" /> 연결 개요
                {overviewLoading && <Spinner label="연결을 그리는 중" size={11} />}
              </h3>
              <GraphView nodes={overview.nodes} edges={overview.edges}
                onNodeClick={openNode} height={420} maxNodes={28} />
            </section>
          )}
          <GraphNodeList nodes={results} onSelect={openNode} />
        </>
      ) : searched && !error ? (
        <EmptyState icon={Network} title="검색 결과가 없습니다"
          description="다른 이름으로 찾아보거나, [설정]에서 검색 인덱스·그래프를 다시 빌드해 보세요." />
      ) : null}
    </div>
  );
}

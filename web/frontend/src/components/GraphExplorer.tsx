import { useState, useEffect } from "react";
import { Search, Loader2, ChevronRight, Network, ArrowLeft, X } from "lucide-react";
import { listGraphNodes, getNodeNeighbors } from "../lib/api";
import type { GraphNode, GraphEdge, GraphNeighbors } from "../lib/types";
import MiniGraph from "./MiniGraph";

// 노드 타입별 색상 (라벨 배지)
const TYPE_TONE: Record<string, string> = {
  person: "bg-sky-100 text-sky-700",
  project: "bg-violet-100 text-violet-700",
  meeting: "bg-emerald-100 text-emerald-700",
  topic: "bg-amber-100 text-amber-700",
  organization: "bg-rose-100 text-rose-700",
  document: "bg-zinc-100 text-zinc-600",
};
const tone = (t: string) => TYPE_TONE[t] || "bg-brand-100 text-brand-700";

function NodeBadge({ node, onClick }: { node: GraphNode; onClick?: () => void }) {
  return (
    <button
      onClick={onClick}
      disabled={!onClick}
      className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-sm border border-brand-200 bg-white hover:bg-brand-50 transition-all ${onClick ? "cursor-pointer" : "cursor-default"}`}
    >
      <span className={`text-[10px] font-bold uppercase px-1.5 py-0.5 rounded ${tone(node.type)}`}>{node.type}</span>
      <span className="font-medium text-brand-900 truncate max-w-[14rem]">{node.label}</span>
      {onClick && <ChevronRight size={14} className="text-brand-400" />}
    </button>
  );
}

export default function GraphExplorer({ initialQuery = "" }: { initialQuery?: string }) {
  const [q, setQ] = useState(initialQuery);
  const [results, setResults] = useState<GraphNode[]>([]);
  const [searching, setSearching] = useState(false);
  const [searched, setSearched] = useState(false);
  const [error, setError] = useState("");

  // 드릴다운 스택 (뒤로가기용)
  const [stack, setStack] = useState<GraphNode[]>([]);
  const [focus, setFocus] = useState<GraphNeighbors | null>(null);
  const [loadingNode, setLoadingNode] = useState(false);

  // 검색 결과 "개요 그래프" — 클릭해 들어가지 않아도 결과가 어떻게 연결돼 있는지 바로 보여준다.
  const [overview, setOverview] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [overviewLoading, setOverviewLoading] = useState(false);

  // 결과 상위 노드들의 1-hop 이웃/엣지를 모아 하나의 그래프로 합친다(중복 제거).
  const loadOverview = async (res: GraphNode[]) => {
    if (!res.length) { setOverview(null); return; }
    setOverviewLoading(true);
    try {
      const seeds = res.slice(0, 7);   // 요청 수 제한(로컬 백엔드 부담·가독성)
      const parts = await Promise.all(
        seeds.map((n) => getNodeNeighbors(n.id, { depth: 1, limit: 24 }).catch(() => null))
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

  const runSearch = async () => {
    setSearching(true); setError(""); setSearched(true);
    setFocus(null); setStack([]);   // 새 검색 시 상세에서 결과 개요로 복귀
    try {
      const nodes = await listGraphNodes({ q: q.trim() || undefined, limit: 50 });
      setResults(nodes);
      void loadOverview(nodes);
    } catch (e: any) {
      setError(e?.message || "검색 실패");
      setResults([]); setOverview(null);
    }
    setSearching(false);
  };

  // 초기 진입: initialQuery(위키링크 클릭 등)가 있으면 그 대상을 검색하고
  // 정확히 일치하는 노드를 자동으로 펼쳐 보여준다. 없으면 상위 노드 목록 표시.
  useEffect(() => {
    const term = (initialQuery || "").trim();
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
        setError(e?.message || "검색 실패");
        setResults([]);
      }
      setSearching(false);
    })();
    /* eslint-disable-next-line */
  }, []);

  const openNode = async (node: GraphNode, pushStack = true) => {
    setLoadingNode(true); setError("");
    try {
      const n = await getNodeNeighbors(node.id, { depth: 1, limit: 100 });
      setFocus(n);
      if (pushStack) setStack((s) => [...s, node]);
    } catch (e: any) {
      setError(e?.message || "노드 조회 실패");
    }
    setLoadingNode(false);
  };

  const goBack = async () => {
    const s = [...stack];
    s.pop();                       // 현재
    const prev = s[s.length - 1];
    setStack(s);
    if (prev) await openNode(prev, false);
    else setFocus(null);
  };

  const closeDetail = () => { setStack([]); setFocus(null); };

  // 이웃을 relation_type 별로 묶기
  const grouped: Record<string, GraphNode[]> = {};
  if (focus) {
    const byId = new Map(focus.neighbors.map((n) => [n.id, n]));
    for (const e of focus.edges) {
      const otherId = e.from_node_id === focus.node?.id ? e.to_node_id : e.from_node_id;
      const other = byId.get(otherId);
      if (!other) continue;
      (grouped[e.relation_type] ||= []).push(other);
    }
  }

  return (
    <div className="max-w-4xl mx-auto px-1 md:px-0">
      <h2 className="text-2xl font-bold tracking-tight mb-1 flex items-center gap-2">
        <Network size={22} /> 지식 그래프 탐색
      </h2>
      <p className="text-sm text-brand-500 mb-4">
        회의·인물·프로젝트·주제가 어떻게 연결돼 있는지 탐색합니다. 노드를 눌러 연결된 이웃으로 이동하세요.
      </p>

      {/* 검색 */}
      <div className="flex gap-2 mb-4">
        <div className="relative flex-1">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-brand-400" />
          <input
            aria-label="지식그래프 노드 검색"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder="이름/키워드로 노드 검색 (비우면 상위 노드)"
            className="w-full pl-9 pr-3 py-2.5 bg-white border border-brand-200 rounded-xl outline-none focus:ring-2 focus:ring-brand-500 text-sm"
          />
        </div>
        <button onClick={runSearch} disabled={searching} className="flex items-center gap-2 px-5 py-2.5 bg-brand-950 text-white rounded-xl text-sm font-bold hover:bg-brand-900 transition-all">
          {searching ? <Loader2 size={16} className="animate-spin" /> : <Search size={16} />} 검색
        </button>
      </div>

      {error && (
        <div className="mb-4 rounded-xl border border-red-200 bg-red-50 text-red-600 px-4 py-3 text-sm">
          {error} — 지식 그래프가 비활성화됐거나 아직 데이터가 없을 수 있습니다.
        </div>
      )}

      {/* 상세(포커스 노드 + 이웃) */}
      {focus && focus.node ? (
        <section className="bg-white border border-brand-200 rounded-2xl p-4 md:p-5 mb-4 shadow-sm">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2 text-sm text-brand-500">
              {stack.length > 1 && (
                <button onClick={goBack} className="flex items-center gap-1 hover:text-brand-900">
                  <ArrowLeft size={15} /> 뒤로
                </button>
              )}
            </div>
            <button onClick={closeDetail} className="text-brand-400 hover:text-brand-900"><X size={18} /></button>
          </div>

          <div className="flex items-center gap-2 mb-1">
            <span className={`text-[11px] font-bold uppercase px-2 py-0.5 rounded ${tone(focus.node.type)}`}>{focus.node.type}</span>
            <h3 className="text-lg font-bold text-brand-950">{focus.node.label}</h3>
          </div>
          {loadingNode && <Loader2 size={16} className="animate-spin text-brand-400 my-2" />}

          {/* 속성 */}
          {focus.node.attributes && Object.keys(focus.node.attributes).length > 0 && (
            <div className="mt-2 mb-4 text-xs text-brand-500 space-y-0.5">
              {Object.entries(focus.node.attributes).slice(0, 8).map(([k, v]) => (
                <div key={k}><span className="font-bold">{k}:</span> {String(v)}</div>
              ))}
            </div>
          )}

          {/* 시각적 그래프 개요 (배지 리스트와 함께 제공) */}
          {focus.neighbors.length > 0 && (
            <div className="mt-3 mb-4 rounded-xl border border-brand-100 bg-brand-50/40">
              <MiniGraph
                nodes={[focus.node, ...focus.neighbors]}
                edges={focus.edges}
                centerId={focus.node.id}
                activeId={focus.node.id}
                onNodeClick={(n) => { if (n.id !== focus.node?.id) openNode(n); }}
              />
            </div>
          )}

          {/* 이웃 (관계별) */}
          {Object.keys(grouped).length > 0 ? (
            <div className="space-y-4 mt-2">
              {Object.entries(grouped).map(([rel, nodes]) => (
                <div key={rel}>
                  <div className="text-xs font-bold text-brand-400 uppercase tracking-widest mb-2">{rel} ({nodes.length})</div>
                  <div className="flex flex-wrap gap-2">
                    {nodes.map((n) => <NodeBadge key={n.id} node={n} onClick={() => openNode(n)} />)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            !loadingNode && <p className="text-sm text-brand-400 mt-2">연결된 이웃이 없습니다.</p>
          )}
        </section>
      ) : (
        /* 검색 결과: 개요 그래프 + 목록 */
        <section>
          {searching ? (
            <div className="flex items-center gap-2 text-brand-400 text-sm py-8 justify-center">
              <Loader2 size={18} className="animate-spin" /> 불러오는 중...
            </div>
          ) : results.length > 0 ? (
            <>
              {/* 검색 즉시 보이는 연결 개요 그래프 — 노드를 클릭하면 그 노드로 들어간다. */}
              {overview && overview.nodes.length > 0 && (
                <div className="mb-4 rounded-2xl border border-brand-200 bg-white shadow-sm overflow-hidden">
                  <div className="flex items-center gap-2 px-4 pt-3 text-xs font-bold text-brand-400 uppercase tracking-widest">
                    <Network size={13} /> 연결 개요
                    {overviewLoading && <Loader2 size={12} className="animate-spin" />}
                  </div>
                  <MiniGraph
                    nodes={overview.nodes}
                    edges={overview.edges}
                    onNodeClick={(n) => openNode(n)}
                    height={420}
                    maxNodes={28}
                  />
                </div>
              )}
              {overviewLoading && !overview && (
                <div className="mb-4 flex items-center gap-2 text-brand-400 text-xs py-6 justify-center">
                  <Loader2 size={14} className="animate-spin" /> 연결 관계를 그리는 중...
                </div>
              )}
              <div className="flex flex-wrap gap-2">
                {results.map((n) => <NodeBadge key={n.id} node={n} onClick={() => openNode(n)} />)}
              </div>
            </>
          ) : searched && !error ? (
            <p className="text-sm text-brand-400 py-8 text-center">검색 결과가 없습니다.</p>
          ) : null}
        </section>
      )}
    </div>
  );
}

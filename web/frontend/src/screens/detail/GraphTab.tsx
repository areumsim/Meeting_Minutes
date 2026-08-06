import React, { useState } from "react";
import { Network } from "lucide-react";
import GraphView, { GraphNodeList } from "../../ui/GraphView";
import { entityLabel } from "../../lib/entities";
import { Spinner, EmptyState } from "../../ui/states";
import { getNodeNeighbors } from "../../lib/api";
import type { SessionGraph, GraphNeighbors } from "../../lib/types";

/**
 * 상세의 그래프 탭 (PRD FR-DET-7).
 *
 * 지식 화면의 그래프와 **같은 렌더러·같은 팔레트**를 쓴다(ui/GraphView + lib/entities).
 * 종전에는 두 화면이 각자 색 표를 갖고 있어 같은 노드가 다른 색이었다.
 *
 * 이웃 조회는 세 상태를 구분한다 — 불러오는 중 / 실패 / 정말 없음. 실패를 '연결된 노드가
 * 없습니다'로 적으면 사실이 아닌 문장을 보여주게 된다.
 */
export default function GraphTab({ graph }: { graph: SessionGraph }) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [cache, setCache] = useState<Record<string, GraphNeighbors>>({});
  const [loadingId, setLoadingId] = useState<string | null>(null);
  const [failed, setFailed] = useState<Record<string, boolean>>({});

  const allNodes = Object.values(graph.nodes).flat();
  const meetingId = graph.nodes.meeting?.[0]?.id;

  const toggle = async (nodeId: string) => {
    if (activeId === nodeId) { setActiveId(null); return; }
    setActiveId(nodeId);
    if (cache[nodeId]) return;
    setLoadingId(nodeId);
    setFailed((p) => ({ ...p, [nodeId]: false }));   // 재시도 시 초기화
    try {
      const result = await getNodeNeighbors(nodeId, { depth: 1 });
      setCache((p) => ({ ...p, [nodeId]: result }));
    } catch {
      setFailed((p) => ({ ...p, [nodeId]: true }));
    } finally {
      setLoadingId(null);
    }
  };

  if (allNodes.length === 0) {
    return <EmptyState icon={Network} title="그래프 데이터가 없습니다"
      description="회의에서 인물·조직·주제가 추출되면 여기에 관계도가 생깁니다." />;
  }

  const active = activeId ? cache[activeId] : null;

  return (
    <div className="space-y-4">
      <div className="rounded-card border border-line bg-surface-2/50">
        <GraphView nodes={allNodes} edges={graph.edges} centerId={meetingId}
          activeId={activeId} onNodeClick={(n) => toggle(n.id)} />
      </div>

      {/* 타입별 섹션 — 그래프를 못 쓰는 사용자의 경로이기도 하다(키보드·스크린리더). */}
      {Object.entries(graph.nodes).map(([type, nodes]) => (
        <section key={type}>
          <h4 className="mb-1.5 text-xs font-semibold text-ink-3">
            {entityLabel(type)} ({nodes.length})
          </h4>
          <GraphNodeList nodes={nodes} activeId={activeId} onSelect={(n) => toggle(n.id)} />
        </section>
      ))}

      {activeId && (
        <div className="rounded-card border border-line bg-surface p-2.5 text-sm">
          {loadingId === activeId ? (
            <Spinner label="연결 정보를 불러오는 중" size={14} />
          ) : failed[activeId] ? (
            <p role="alert" className="text-rec">
              연결 정보를 불러오지 못했습니다. 닫고 다시 열면 재시도합니다.
            </p>
          ) : active?.neighbors.length ? (
            <ul className="space-y-0.5">
              {active.neighbors.map((n) => (
                <li key={n.id} className="text-ink-2">
                  <span className="text-ink-3">{entityLabel(n.type)}:</span> {n.label}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-ink-3">연결된 노드가 없습니다.</p>
          )}
        </div>
      )}
    </div>
  );
}

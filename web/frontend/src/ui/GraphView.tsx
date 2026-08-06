import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphNode, GraphEdge } from "../lib/types";
import { entityColor, entityLabel } from "../lib/entities";

// 자체 force-directed 지식 그래프 — 외부 라이브러리 없음(오프라인 exe 번들 안전).
// 스프링(엣지)·반발(노드쌍)·중심 중력으로 스스로 배치되며, 노드를 드래그하면 그래프가
// 살아 움직이고, 호버하면 연결된 이웃/엣지를 강조한다. 물리는 순수 JS(브라우저).
//
// 색·라벨은 **자기 표를 갖지 않는다** — `lib/entities.ts` 하나를 본다(PRD 리뷰 P2-3).
// 종전에는 여기(8종 hex)·GraphExplorer(6종 tailwind)·SessionDetail(7종 라벨)이 각자
// 표를 갖고 있어서 같은 '노트' 노드가 화면마다 다른 색이었고, 원시 hex 라 다크 모드에서
// 라이트 값이 그대로 남았다. 이제 전부 `var(--color-ent-*)` 라 테마를 따라간다.

/** 노드 채움 — 토큰 색을 배경에 아주 옅게 섞는다(테두리·글자는 토큰 색 그대로). */
const fillOf = (t: string) =>
  `color-mix(in srgb, ${entityColor(t)} 14%, var(--color-surface))`;
const truncate = (s: string, n = 12) => (s && s.length > n ? s.slice(0, n - 1) + "…" : s || "");

interface SimNode {
  id: string;
  node: GraphNode;
  x: number; y: number;
  vx: number; vy: number;
  fx: number | null; fy: number | null;   // 고정(중심 또는 드래그 중)
  center: boolean;
  r: number;
}

interface Props {
  nodes: GraphNode[];
  edges: GraphEdge[];
  centerId?: string;
  activeId?: string | null;
  onNodeClick?: (node: GraphNode) => void;
  height?: number;
  maxNodes?: number;
}

const W = 640;

export default function MiniGraph({
  nodes, edges, centerId, activeId, onNodeClick, height = 400, maxNodes = 24,
}: Props) {
  const svgRef = useRef<SVGSVGElement | null>(null);
  const simRef = useRef<{ list: SimNode[]; edges: { a: SimNode; b: SimNode; e: GraphEdge }[]; hidden: number } | null>(null);
  const alphaRef = useRef(1);
  const rafRef = useRef<number | null>(null);
  const runningRef = useRef(false);
  const dragRef = useRef<{ n: SimNode; moved: boolean } | null>(null);
  const clickRef = useRef(onNodeClick);
  clickRef.current = onNodeClick;

  const [, setFrame] = useState(0);
  const [hoverId, setHoverId] = useState<string | null>(null);

  // 그래프 "내용"이 실제로 바뀔 때만 시뮬레이션을 재초기화(부모 리렌더로 인한 리셋 방지).
  const sig = useMemo(
    () => nodes.map((n) => n.id).join(",") + "|" + edges.map((e) => e.id).join(","),
    [nodes, edges]
  );

  useEffect(() => {
    if (!nodes || nodes.length === 0) { simRef.current = null; setFrame((f) => f + 1); return; }

    const cx = W / 2, cy = height / 2;

    // 연결 차수로 중심/우선순위 결정
    const degree = new Map<string, number>();
    for (const n of nodes) degree.set(n.id, 0);
    for (const e of edges) {
      degree.set(e.from_node_id, (degree.get(e.from_node_id) || 0) + 1);
      degree.set(e.to_node_id, (degree.get(e.to_node_id) || 0) + 1);
    }
    let center = (centerId && nodes.find((n) => n.id === centerId)) || null;
    if (!center) {
      center = nodes.find((n) => n.type === "meeting") ||
        [...nodes].sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0))[0];
    }
    const others = nodes
      .filter((n) => n.id !== center!.id)
      .sort((a, b) => (degree.get(b.id) || 0) - (degree.get(a.id) || 0));
    const shown = others.slice(0, maxNodes);
    const hidden = others.length - shown.length;

    // 초기 배치: 중심은 가운데 고정, 나머지는 원형 + 약간의 흔들림(자연스러운 펼침).
    const list: SimNode[] = [];
    const byId = new Map<string, SimNode>();
    const mk = (node: GraphNode, x: number, y: number, isCenter: boolean): SimNode => {
      const s: SimNode = {
        id: node.id, node, x, y, vx: 0, vy: 0,
        fx: isCenter ? cx : null, fy: isCenter ? cy : null,
        center: isCenter, r: isCenter ? 30 : 22,
      };
      list.push(s); byId.set(node.id, s); return s;
    };
    mk(center, cx, cy, true);
    const R = Math.min(W, height) / 2 - 80;
    shown.forEach((node, i) => {
      const ang = -Math.PI / 2 + (i / Math.max(1, shown.length)) * Math.PI * 2;
      const jitter = (Math.random() - 0.5) * 30;
      mk(node, cx + (R + jitter) * Math.cos(ang), cy + (R + jitter) * Math.sin(ang), false);
    });

    const simEdges = edges
      .map((e) => {
        const a = byId.get(e.from_node_id); const b = byId.get(e.to_node_id);
        return a && b ? { a, b, e } : null;
      })
      .filter(Boolean) as { a: SimNode; b: SimNode; e: GraphEdge }[];

    simRef.current = { list, edges: simEdges, hidden };
    alphaRef.current = 1;
    startLoop();

    return () => stopLoop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig, height, maxNodes]);

  const marginX = 44, marginY = 50;

  function tick() {
    const sim = simRef.current;
    if (!sim) return;
    const cx = W / 2, cy = height / 2;
    const list = sim.list;
    const alpha = alphaRef.current;

    // 반발력 (모든 노드쌍)
    for (let i = 0; i < list.length; i++) {
      for (let j = i + 1; j < list.length; j++) {
        const a = list[i], b = list[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); d2 = 1; }
        const d = Math.sqrt(d2);
        const rep = 2600 / d2;              // ~ 1/거리^2
        const ux = dx / d, uy = dy / d;
        a.vx += ux * rep; a.vy += uy * rep;
        b.vx -= ux * rep; b.vy -= uy * rep;
      }
    }
    // 스프링 (엣지)
    const rest = 96;
    for (const { a, b } of sim.edges) {
      const dx = b.x - a.x, dy = b.y - a.y;
      const d = Math.max(0.5, Math.hypot(dx, dy));
      const f = 0.035 * (d - rest);
      const ux = dx / d, uy = dy / d;
      a.vx += ux * f; a.vy += uy * f;
      b.vx -= ux * f; b.vy -= uy * f;
    }
    // 중심 중력 + 적분
    let motion = 0;
    for (const n of list) {
      n.vx += (cx - n.x) * 0.008;
      n.vy += (cy - n.y) * 0.008;
      n.vx *= 0.82; n.vy *= 0.82;                 // 감쇠
      if (n.fx != null) { n.x = n.fx; n.vx = 0; }
      else { n.x += n.vx * alpha; }
      if (n.fy != null) { n.y = n.fy; n.vy = 0; }
      else { n.y += n.vy * alpha; }
      n.x = Math.max(marginX, Math.min(W - marginX, n.x));
      n.y = Math.max(marginY, Math.min(height - marginY, n.y));
      motion += Math.abs(n.vx) + Math.abs(n.vy);
    }
    alphaRef.current = Math.max(0, alpha * 0.992);
    return motion;
  }

  function loop() {
    const motion = tick();
    setFrame((f) => (f + 1) % 1000000);
    // 드래그 중이거나 아직 충분히 움직이면 계속. 아니면 정지(리소스 절약).
    if (dragRef.current || (motion != null && motion > 0.6 && alphaRef.current > 0.02)) {
      rafRef.current = requestAnimationFrame(loop);
    } else {
      runningRef.current = false;
    }
  }
  function startLoop() {
    if (runningRef.current) return;
    runningRef.current = true;
    rafRef.current = requestAnimationFrame(loop);
  }
  function stopLoop() {
    runningRef.current = false;
    if (rafRef.current != null) cancelAnimationFrame(rafRef.current);
    rafRef.current = null;
  }
  function reheat(a = 0.5) {
    alphaRef.current = Math.max(alphaRef.current, a);
    startLoop();
  }

  useEffect(() => () => stopLoop(), []);

  // ── 드래그 (client 좌표 → SVG 좌표) ──
  function toSvg(clientX: number, clientY: number) {
    const svg = svgRef.current;
    if (!svg) return { x: 0, y: 0 };
    const ctm = svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const pt = svg.createSVGPoint();
    pt.x = clientX; pt.y = clientY;
    const p = pt.matrixTransform(ctm.inverse());
    return { x: p.x, y: p.y };
  }

  function onNodePointerDown(e: React.PointerEvent, n: SimNode) {
    e.preventDefault();
    const p = toSvg(e.clientX, e.clientY);
    n.fx = p.x; n.fy = p.y; n.x = p.x; n.y = p.y;
    dragRef.current = { n, moved: false };
    reheat(0.6);

    const move = (ev: PointerEvent) => {
      const d = dragRef.current; if (!d) return;
      const q = toSvg(ev.clientX, ev.clientY);
      d.n.fx = q.x; d.n.fy = q.y; d.n.x = q.x; d.n.y = q.y;
      d.moved = true;
      reheat(0.5);
    };
    const up = () => {
      const d = dragRef.current;
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      dragRef.current = null;
      if (d) {
        // 중심이 아니면 놓아 자유롭게 떠다니게 한다.
        if (!d.n.center) { d.n.fx = null; d.n.fy = null; }
        // 이동이 거의 없었으면 '클릭'으로 간주.
        if (!d.moved) clickRef.current?.(d.n.node);
      }
      reheat(0.25);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  }

  const sim = simRef.current;
  if (!sim) return null;

  // 호버 강조 대상(호버 노드 + 직접 이웃)
  const hiSet = new Set<string>();
  if (hoverId) {
    hiSet.add(hoverId);
    for (const { a, b } of sim.edges) {
      if (a.id === hoverId) hiSet.add(b.id);
      if (b.id === hoverId) hiSet.add(a.id);
    }
  }
  const dim = (id: string) => hoverId != null && !hiSet.has(id);

  return (
    <div className="w-full overflow-x-auto select-none">
      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${height}`}
        width="100%"
        style={{ maxWidth: "100%", height: "auto", display: "block", touchAction: "none" }}
        role="img"
        aria-label="지식 그래프 다이어그램 (노드를 끌어 움직일 수 있습니다)"
      >
        {/* 엣지 */}
        {sim.edges.map(({ a, b, e }, i) => {
          const hot = hoverId != null && (a.id === hoverId || b.id === hoverId);
          const faded = hoverId != null && !hot;
          return (
            <line
              key={i}
              x1={a.x} y1={a.y} x2={b.x} y2={b.y}
              stroke={hot ? "var(--color-accent)" : "var(--color-line-strong)"}
              strokeWidth={hot ? 2 : 1.4}
              opacity={faded ? 0.2 : 1}
            >
              <title>{e.relation_type}</title>
            </line>
          );
        })}
        {/* 엣지 라벨(호버한 노드에 연결된 것만 — 평소엔 감춰 깔끔하게) */}
        {hoverId != null && sim.edges.map(({ a, b, e }, i) => {
          if (a.id !== hoverId && b.id !== hoverId) return null;
          return (
            <text
              key={"l" + i}
              x={(a.x + b.x) / 2} y={(a.y + b.y) / 2 - 3}
              textAnchor="middle" fontSize={8.5} fontWeight={700} fill="var(--color-accent)"
              style={{ pointerEvents: "none" }}
            >
              {e.relation_type}
            </text>
          );
        })}
        {/* 노드 */}
        {sim.list.map((p) => {
          const c = entityColor(p.node.type);
          const active = p.center || activeId === p.node.id || hoverId === p.id;
          const faded = dim(p.id);
          return (
            <g
              key={p.id}
              transform={`translate(${p.x} ${p.y})`}
              style={{ cursor: "grab", opacity: faded ? 0.35 : 1, transition: "opacity 120ms" }}
              onPointerDown={(e) => onNodePointerDown(e, p)}
              onPointerEnter={() => setHoverId(p.id)}
              onPointerLeave={() => setHoverId((h) => (h === p.id ? null : h))}
            >
              <title>{`${entityLabel(p.node.type)}: ${p.node.label}`}</title>
              <circle
                r={p.r}
                fill={fillOf(p.node.type)}
                stroke={c}
                strokeWidth={active ? 3 : 1.6}
              />
              <text
                textAnchor="middle" dominantBaseline="central"
                fontSize={p.center ? 12 : 10.5}
                fontWeight={p.center ? 700 : 500}
                fill="var(--color-ink)"
                style={{ pointerEvents: "none" }}
              >
                {truncate(p.node.label, p.center ? 12 : 10)}
              </text>
              {/* 타입은 원문(person)이 아니라 한국어 라벨(인물)로 — 화면 기본 언어가 한국어다.
                  색만으로 타입을 구분하지 않는다는 규칙(PRD §5.5)의 글자 축이기도 하다. */}
              <text
                y={p.r + 12} textAnchor="middle"
                fontSize={8.5} fontWeight={700} letterSpacing={0.4} fill={c}
                style={{ pointerEvents: "none" }}
              >
                {entityLabel(p.node.type)}
              </text>
            </g>
          );
        })}
        {sim.hidden > 0 && (
          <text x={W - 10} y={height - 10} textAnchor="end" fontSize={11} fill="var(--color-ink-3)">
            +{sim.hidden}개 더 (아래 목록에서 전체 확인)
          </text>
        )}
      </svg>
      <p className="-mt-1 px-3 pb-2 text-xs text-ink-3">
        노드를 <b>끌어서</b> 움직이거나 <b>클릭</b>해 이동 · 노드에 커서를 올리면 연결 관계가 강조됩니다.
      </p>
    </div>
  );
}

/**
 * 그래프의 **키보드 경로**(PRD FR-KNO-3·§5.5).
 *
 * 위 SVG 는 `role="img"` 이라 포인터로만 다룰 수 있다 — 드래그로 도는 물리 시뮬레이션을
 * 키보드로 조작 가능하게 만드는 것은 무리이고, 그럴 필요도 없다. 대신 **같은 노드 집합을
 * 버튼 목록으로도** 낸다. 그래프를 못 쓰는 사용자도 모든 노드에 도달하고, 각 버튼은
 * "인물 심아름, 연결 탐색" 처럼 타입을 포함한 이름을 갖는다(아이콘·색이 안 보여도 구분된다).
 *
 * 시각적으로도 감추지 않는다 — 그래프 옆의 노드 칩 목록은 마우스 사용자에게도 유용하고,
 * 숨긴 접근성 경로는 곧 썩는다(아무도 눈으로 확인하지 않는다).
 */
export function GraphNodeList({
  nodes, activeId, onSelect, emptyText = "표시할 노드가 없습니다.",
}: {
  nodes: GraphNode[];
  activeId?: string | null;
  onSelect: (node: GraphNode) => void;
  emptyText?: string;
}) {
  if (nodes.length === 0) {
    return <p className="text-sm text-ink-3">{emptyText}</p>;
  }
  return (
    <ul className="flex flex-wrap gap-1.5">
      {nodes.map((n) => (
        <li key={n.id}>
          <button
            type="button"
            onClick={() => onSelect(n)}
            aria-label={`${entityLabel(n.type)} ${n.label}, 연결 탐색`}
            aria-current={activeId === n.id ? "true" : undefined}
            className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-sm
              transition-colors hover:bg-hover ${
                activeId === n.id
                  ? "border-accent bg-accent-weak text-accent"
                  : "border-line-strong bg-surface text-ink"
              }`}
          >
            <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: entityColor(n.type) }} />
            <span className="truncate">{n.label}</span>
            <span className="text-xs text-ink-3">{entityLabel(n.type)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

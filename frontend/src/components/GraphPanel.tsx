import { useEffect, useRef, useState } from "react";
import cytoscape, { type Core, type ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { GraphPayload } from "../types";
import { colorFor, DIMENSION_COLORS } from "../theme";
import type { Theme } from "../useTheme";

cytoscape.use(fcose);

interface Props {
  graph: GraphPayload | null;
  onSelectNode: (nodeId: string) => void;
  selectedId: string | null;
  theme: Theme;
}

const USER_ID = "__user__";
const LABEL_ZOOM_THRESHOLD = 0.6;
const LABEL_NODE_LIMIT = 40;

interface Palette {
  nodeText: string; outline: string; edge: string; edgeStrong: string;
  userBg: string; userInk: string; hubInk: string; labelBg: string; edgeLabel: string; userRing: string;
}

function palette(theme: Theme): Palette {
  return theme === "dark"
    ? { nodeText: "#e8e9ee", outline: "#0b0d12", edge: "#3a3d44", edgeStrong: "#4a4d55",
        userBg: "#eceef3", userInk: "#0b0d12", hubInk: "#0b0d12", labelBg: "#0b0d12", edgeLabel: "#6b7280", userRing: "rgba(236,238,243,0.22)" }
    : { nodeText: "#242a36", outline: "#ffffff", edge: "#cbd0d8", edgeStrong: "#b4bac4",
        userBg: "#14161d", userInk: "#ffffff", hubInk: "#0b0d12", labelBg: "#ffffff", edgeLabel: "#8a8f98", userRing: "rgba(20,22,29,0.18)" };
}

// A deliberately quiet three-tier graph: YOU is the visual anchor, category
// hubs organise the space, and memories stay small enough to read as a map.
function styleSheet(theme: Theme): cytoscape.StylesheetStyle[] {
  const P = palette(theme);
  const selRing = theme === "dark" ? "#ffffff" : "#111827";
  return [
    {
      selector: "node",
      style: {
        label: "data(display)",
        color: P.nodeText,
        "font-family": "Manrope, system-ui, sans-serif",
        "font-size": 10, "font-weight": 500,
        "text-wrap": "wrap", "text-max-width": "116px",
        "text-valign": "bottom", "text-margin-y": 8,
        "text-outline-color": P.outline, "text-outline-width": 3,
        "min-zoomed-font-size": 7, // labels fade out when zoomed far away
        "border-width": 0,
        "transition-property": "opacity, width, height", "transition-duration": 120,
      } as cytoscape.Css.Node,
    },
    // A memory is a small luminous point. Confidence changes its size, not its
    // visual weight, so dense graphs remain calm.
    {
      selector: 'node[kind = "value"]',
      style: {
        "background-color": (e) => colorFor(e.data("dimension")),
        "background-opacity": 1,
        width: "mapData(conf, 0, 1, 12, 30)",
        height: "mapData(conf, 0, 1, 12, 30)",
        "overlay-color": (e) => colorFor(e.data("dimension")),
        "overlay-opacity": 0.1, "overlay-padding": 6,
        "border-width": 1, "border-color": P.outline,
      } as cytoscape.Css.Node,
    },
    // Hubs define a cluster without competing with its memories.
    {
      selector: 'node[kind = "hub"]',
      style: {
        "background-color": (e) => colorFor(e.data("dimension")),
        "background-opacity": 0.16,
        "border-width": 2, "border-color": (e) => colorFor(e.data("dimension")),
        width: 28, height: 28,
        "font-size": 8, "font-weight": 700, color: P.nodeText,
        "text-transform": "uppercase", "text-margin-y": 10,
      } as cytoscape.Css.Node,
    },
    // The centre reads as an anchor rather than another graph node.
    {
      selector: `node[kind = "user"]`,
      style: {
        "background-color": P.userBg, width: 60, height: 60, label: "YOU",
        color: P.userInk, "font-size": 13, "font-weight": 800,
        "text-valign": "center", "text-margin-y": 0, "text-outline-width": 0,
        "border-width": 9, "border-color": P.userRing,
      } as cytoscape.Css.Node,
    },
    // Superseded: faded solid dot (history layer).
    {
      selector: 'node[status = "superseded"]',
      style: { opacity: 0.3, color: P.edgeLabel },
    },
    // A focused node gets a modest halo; the rest of the graph recedes.
    {
      selector: "node:selected, node.match",
      style: {
        "overlay-color": selRing, "overlay-opacity": 0.18, "overlay-padding": 7,
      } as cytoscape.Css.Node,
    },
    { selector: "node.nolabel", style: { label: "" } },
    { selector: ".faded", style: { opacity: 0.12, "text-opacity": 0.12 } },
    { selector: ".focus-link", style: { "line-opacity": 0.95, width: 1.5 } },
    // Structural links: thin, uniform, subtle (no arrows, no labels).
    {
      selector: 'edge[kind = "tier"], edge[kind = "member"]',
      style: {
        width: 1, "line-color": P.edge, "line-opacity": theme === "dark" ? 0.22 : 0.42,
        "curve-style": "straight", "target-arrow-shape": "none",
      } as cytoscape.Css.Edge,
    },
    // Supersession links are the only relationship that gets a label.
    {
      selector: 'edge[kind = "supersedes"]',
      style: {
        width: 1, "line-color": P.edgeStrong, "line-style": "dashed",
        "line-opacity": 0.55, "curve-style": "bezier", "target-arrow-shape": "none",
        label: "superseded", color: P.edgeLabel, "font-size": 8,
        "text-background-color": P.labelBg, "text-background-opacity": 0.85,
        "text-background-padding": "2px",
      } as cytoscape.Css.Edge,
    },
    // PART 8: typed inter-node relations — solid, darker, arrowed, labelled.
    {
      selector: 'edge[kind = "relation"]',
      style: {
        width: 1.6, "line-color": P.edgeStrong, "line-opacity": 0.9,
        "curve-style": "bezier", "target-arrow-shape": "triangle",
        "target-arrow-color": P.edgeStrong, "arrow-scale": 0.8,
        label: "data(relation)", color: P.edgeLabel, "font-size": 8,
        "text-background-color": P.labelBg, "text-background-opacity": 0.85,
        "text-background-padding": "2px", "text-rotation": "autorotate",
      } as cytoscape.Css.Edge,
    },
    // A negated relation ("don't get along") reads as a red dashed link.
    {
      selector: 'edge[kind = "relation"][polarity = "negative"]',
      style: {
        "line-color": "#fb8a8a", "line-style": "dashed", "target-arrow-color": "#fb8a8a",
      } as cytoscape.Css.Edge,
    },
  ];
}

function toElements(graph: GraphPayload, showHistory: boolean): ElementDefinition[] {
  const all = graph.nodes.map((n) => n.data);
  const rendered = all.filter((d) => d.status === "active" || (showHistory && d.status === "superseded"));
  const renderedIds = new Set(rendered.map((d) => d.id));
  const dims = Array.from(new Set(rendered.map((d) => d.dimension)));

  const els: ElementDefinition[] = [{ data: { id: USER_ID, kind: "user" } }];
  for (const dim of dims) {
    els.push({ data: { id: `hub__${dim}`, kind: "hub", dimension: dim, display: dim.replace(/_/g, " ") } });
    els.push({ data: { id: `tier__${dim}`, source: USER_ID, target: `hub__${dim}`, kind: "tier", dimension: dim } });
  }
  for (const d of rendered) {
    const value = d.value.length > 24 ? d.value.slice(0, 23) + "…" : d.value;
    els.push({ data: { ...d, kind: "value", conf: d.confidence_calibrated, display: value } });
    if (d.status === "active") {
      els.push({ data: { id: `mem__${d.id}`, source: `hub__${d.dimension}`, target: d.id, kind: "member", dimension: d.dimension } });
    }
  }
  for (const e of graph.edges) {
    if (!renderedIds.has(e.data.source) || !renderedIds.has(e.data.target)) continue;
    if ((e.data as any).kind === "relation") {
      els.push({ data: { ...e.data, kind: "relation" } });
    } else if (showHistory) {
      els.push({ data: { ...e.data, kind: "supersedes" } });
    }
  }
  return els;
}

export default function GraphPanel({ graph, onSelectNode, selectedId, theme }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const firstRun = useRef(true);
  const queryRef = useRef("");
  const countRef = useRef(0);
  const [showHistory, setShowHistory] = useState(false);
  const [query, setQuery] = useState("");

  function fitGraph() {
    const cy = cyRef.current;
    if (cy && cy.elements().length) cy.animate({ fit: { eles: cy.elements(), padding: 48 }, duration: 250 });
  }

  function applyFilter(cy: Core, q: string) {
    cy.batch(() => {
      cy.elements().removeClass("faded match");
      const term = q.trim().toLowerCase();
      if (!term) return;
      const matches = cy.nodes().filter((n) => {
        const d = n.data();
        return [d.value, d.key, d.dimension, d.display].filter(Boolean)
          .some((v: string) => String(v).toLowerCase().includes(term));
      });
      if (matches.length === 0) return;
      cy.elements().addClass("faded");
      matches.removeClass("faded").addClass("match");
      matches.connectedEdges().removeClass("faded");
      matches.neighborhood().removeClass("faded");
    });
    if (q.trim()) {
      const m = cy.nodes(".match");
      if (m.length) cy.animate({ fit: { eles: m, padding: 80 }, duration: 300 });
    }
  }

  function applyLabelVisibility(cy: Core) {
    const hide = countRef.current > LABEL_NODE_LIMIT && cy.zoom() < LABEL_ZOOM_THRESHOLD;
    cy.nodes().forEach((n) => {
      if (n.data("kind") === "user") return;
      n.toggleClass("nolabel", hide);
    });
  }

  // Init once.
  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cytoscape({
      container: containerRef.current,
      style: styleSheet(theme),
      minZoom: 0.2, maxZoom: 3, wheelSensitivity: 0.2,
    });
    cy.on("tap", "node", (evt) => {
      if (evt.target.data().kind === "value") onSelectNode(evt.target.id());
    });
    cy.on("mouseover", "node", (evt) => {
      const nb = evt.target.closedNeighborhood();
      cy.elements().addClass("faded");
      nb.removeClass("faded");
      evt.target.connectedEdges().addClass("focus-link");
    });
    cy.on("mouseout", "node", () => {
      cy.elements().removeClass("focus-link");
      applyFilter(cy, queryRef.current);
    });
    cy.on("zoom", () => applyLabelVisibility(cy));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-style on theme change (keeps layout/positions).
  useEffect(() => {
    const cy = cyRef.current;
    if (cy) cy.style(styleSheet(theme));
  }, [theme]);

  // Incrementally sync elements when data or history toggle changes: add new,
  // remove gone, update changed. Only re-run layout when the node/edge SET
  // changed — a confidence bump updates data in place without re-jiggling.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;
    const desired = toElements(graph, showHistory);
    countRef.current = desired.filter((e) => !(e.data as any).source).length;
    const desiredIds = new Set(desired.map((e) => (e.data as any).id as string));

    let structureChanged = false;
    cy.batch(() => {
      // Remove elements that no longer exist.
      cy.elements().forEach((el) => {
        if (!desiredIds.has(el.id())) {
          el.remove();
          structureChanged = true;
        }
      });
      // Add new elements; update data on existing ones (confidence/status).
      const toAdd: ElementDefinition[] = [];
      desired.forEach((e) => {
        const existing = cy.getElementById((e.data as any).id);
        if (existing && existing.length) existing.data(e.data);
        else toAdd.push(e);
      });
      if (toAdd.length) {
        cy.add(toAdd);
        structureChanged = true;
      }
    });

    if (structureChanged || firstRun.current) {
      cy.layout({
        name: "fcose", quality: "proof", randomize: firstRun.current,
        animate: true, animationDuration: 600, animationEasing: "ease-out",
        nodeRepulsion: 10500,
        idealEdgeLength: (e: any) => (e.data("kind") === "member" ? 78 : 138),
        edgeElasticity: 0.32, gravity: 0.3, gravityRange: 2.8,
        nodeSeparation: 110, packComponents: true, tile: true, padding: 72,
      } as any).run();
      firstRun.current = false;
    }
    applyLabelVisibility(cy);
    applyFilter(cy, queryRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, showHistory]);

  useEffect(() => {
    queryRef.current = query;
    const cy = cyRef.current;
    if (cy) applyFilter(cy, query);
  }, [query]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.nodes().unselect();
    if (selectedId) {
      const n = cy.getElementById(selectedId);
      if (n) n.select();
    }
  }, [selectedId]);

  const empty = !graph || graph.nodes.length === 0;
  const hasHistory = !!graph && graph.nodes.some((n) => n.data.status === "superseded");
  const visibleDimensions = graph
    ? Array.from(new Set(graph.nodes
      .filter((n) => n.data.status === "active" || (showHistory && n.data.status === "superseded"))
      .map((n) => n.data.dimension)))
    : [];
  const visibleCount = graph
    ? graph.nodes.filter((n) => n.data.status === "active" || (showHistory && n.data.status === "superseded")).length
    : 0;

  return (
    <div className="graph-wrap">
      <div className="graph-controls">
        <div className="graph-title" aria-hidden="true">
          <span className="graph-title-dot" />
          <span>Memory graph</span>
          {!empty && <small>{visibleCount} nodes</small>}
        </div>
        <input
          className="graph-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search nodes…"
        />
        <label className={`history-toggle ${!hasHistory ? "disabled" : ""}`}>
          <input type="checkbox" checked={showHistory} disabled={!hasHistory}
                 onChange={(e) => setShowHistory(e.target.checked)} />
          Show history
        </label>
        <button className="graph-fit" onClick={fitGraph} title="Fit graph to view" aria-label="Fit graph to view">Fit</button>
      </div>
      <div ref={containerRef} className="graph-canvas" />
      {!empty && (
        <div className="graph-legend">
          {visibleDimensions.map((dim) => (
            [dim, DIMENSION_COLORS[dim] ?? "#94a3b8"] as const
          )).map(([dim, c]) => (
            <span key={dim} className="glegend-item">
              <span className="gswatch" style={{ background: c }} />
              {dim.replace(/_/g, " ")}
            </span>
          ))}
          <span className="glegend-note">size = confidence · dashed = superseded</span>
        </div>
      )}
      {empty && (
        <div className="graph-empty">
          <p>The graph is empty</p>
          <p className="muted">
            Share something durable about yourself — a preference, goal, project,
            or habit — and it will appear here, linked to you.
          </p>
        </div>
      )}
    </div>
  );
}

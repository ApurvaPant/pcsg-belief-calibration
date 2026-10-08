import { useState } from "react";
import type { CSSProperties } from "react";
import type { MemoryNode, MemoryPayload } from "../types";
import { DIMENSION_ORDER, colorFor } from "../theme";
import { DimensionIcon, IconTrash, IconUndo } from "./icons";

interface Props {
  memory: MemoryPayload | null;
  loading: boolean;
  onClose: () => void;
  onForget: (nodeId: string) => Promise<void> | void;
  onRestore: (nodeId: string) => Promise<void> | void;
}

function since(ts: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function statusWord(n: MemoryNode): string {
  if (n.status === "active") return "current";
  if (n.status_detail === "ended") return "no longer";
  if (n.status_detail === "achieved") return "achieved";
  if (n.status_detail === "abandoned") return "abandoned";
  return "replaced";
}

function MemoryCard({ n, onForget, onRestore }: {
  n: MemoryNode;
  onForget: (id: string) => Promise<void> | void;
  onRestore: (id: string) => Promise<void> | void;
}) {
  const [confirming, setConfirming] = useState(false);
  const [working, setWorking] = useState(false);
  const color = colorFor(n.dimension);
  const conf = Math.round(n.confidence_calibrated * 100);
  const past = n.status !== "active";

  return (
    <div className={`mem-card ${past ? "past" : ""}`} style={{ ["--dim" as string]: color } as CSSProperties}>
      <div className="mem-card-top">
        <div className="mem-card-main">
          <div className="mem-value">{n.value}</div>
          <div className="mem-key">
            {n.key.replace(/_/g, " ")}
            {past && <span className="mem-status">· {statusWord(n)}</span>}
          </div>
        </div>
        <span className="mem-conf" title={`calibrated confidence · ${n.evidence_count} mention${n.evidence_count === 1 ? "" : "s"}`}>
          {conf}%
        </span>
      </div>

      {n.attributes.length > 0 && (
        <div className="mem-attrs">
          {n.attributes.map((a, i) => (
            <span key={i} className="mem-attr">
              <span className="mem-attr-k">{a.name.replace(/_/g, " ")}</span>
              <span className="mem-attr-v">{a.value}{a.unit ? ` ${a.unit}` : ""}</span>
            </span>
          ))}
        </div>
      )}

      {n.quote && <blockquote className="mem-quote">“{n.quote}”</blockquote>}

      <div className="mem-card-foot">
        <span className="mem-since">
          {past ? `until ${since(n.valid_to || n.valid_from)}` : `since ${since(n.valid_from)}`}
        </span>
        <span className="mem-actions">
          {past && (
            <button
              className="mem-btn"
              disabled={working}
              onClick={async () => { setWorking(true); try { await onRestore(n.id); } finally { setWorking(false); } }}
            >
              <IconUndo size={13} /> Restore
            </button>
          )}
          {confirming ? (
            <span className="mem-confirm">
              <span>Forget?</span>
              <button
                className="mem-btn danger"
                disabled={working}
                onClick={async () => { setWorking(true); try { await onForget(n.id); } finally { setWorking(false); setConfirming(false); } }}
              >
                Yes
              </button>
              <button className="mem-btn" disabled={working} onClick={() => setConfirming(false)}>No</button>
            </span>
          ) : (
            <button className="mem-btn ghost-danger" onClick={() => setConfirming(true)}>
              <IconTrash size={13} /> Forget
            </button>
          )}
        </span>
      </div>
    </div>
  );
}

export default function MemoryPage({ memory, loading, onClose, onForget, onRestore }: Props) {
  const nodes = memory?.nodes ?? [];
  const byDim = new Map<string, MemoryNode[]>();
  for (const n of nodes) {
    if (!byDim.has(n.dimension)) byDim.set(n.dimension, []);
    byDim.get(n.dimension)!.push(n);
  }
  // Known dimensions first (in palette order), then any extras.
  const dims = [
    ...DIMENSION_ORDER.filter((d) => byDim.has(d)),
    ...[...byDim.keys()].filter((d) => !DIMENSION_ORDER.includes(d)),
  ];
  for (const list of byDim.values()) {
    list.sort((a, b) => (a.status === "active" ? 0 : 1) - (b.status === "active" ? 0 : 1));
  }
  const c = memory?.counts;

  return (
    <div className="memory">
      <header className="mem-header">
        <div className="mem-head-text">
          <h1 className="mem-title">Your memory</h1>
          <p className="mem-sub">
            Everything this assistant has learned and modelled about you. Forget anything you’d
            rather it didn’t keep — the change is yours to make.
          </p>
          {c && (
            <div className="mem-counts">
              <span><b>{c.active}</b> current</span>
              <span><b>{c.past}</b> past</span>
              <span><b>{dims.length}</b> dimension{dims.length === 1 ? "" : "s"}</span>
            </div>
          )}
        </div>
        <button className="mem-close" onClick={onClose} aria-label="Back to chat">✕</button>
      </header>

      <div className="mem-body">
        {loading && <p className="muted small mem-empty">Loading your memory…</p>}
        {!loading && nodes.length === 0 && (
          <div className="mem-empty-state">
            <p className="mem-empty-title">Nothing learned yet</p>
            <p className="muted">
              Tell the assistant about a preference, goal, project or habit and it’ll show up here.
            </p>
          </div>
        )}
        {dims.map((dim) => (
          <section key={dim} className="mem-group">
            <div className="mem-group-head">
              <span className="mem-ico" style={{ color: colorFor(dim) }}>
                <DimensionIcon name={dim} size={17} />
              </span>
              <h2 className="mem-group-title">{dim.replace(/_/g, " ")}</h2>
              <span className="mem-group-count">{byDim.get(dim)!.length}</span>
            </div>
            <div className="mem-grid">
              {byDim.get(dim)!.map((n) => (
                <MemoryCard key={n.id} n={n} onForget={onForget} onRestore={onRestore} />
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

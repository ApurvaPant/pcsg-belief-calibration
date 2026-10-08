import type { CSSProperties } from "react";
import type { NodeDetail as NodeDetailType, UserNode } from "../types";
import { colorFor } from "../theme";

interface Props {
  detail: NodeDetailType | null;
  loading: boolean;
  onClose: () => void;
  onRestore?: (nodeId: string) => void;
}

function day(ts: string | null): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function statusWord(n: UserNode): string {
  if (n.status === "active") return "current";
  if (n.status_detail === "ended") return "no longer";
  if (n.status_detail === "achieved") return "achieved";
  if (n.status_detail === "abandoned") return "abandoned";
  return "replaced";
}

function transition(n: UserNode): string {
  if (n.previous_node_id) return "replaced the earlier value";
  return "first learned";
}

export default function NodeDetail({ detail, loading, onClose, onRestore }: Props) {
  if (!detail && !loading) return null;
  const color = detail ? colorFor(detail.dimension) : "var(--muted)";
  const conf = detail ? Math.round(detail.confidence_calibrated * 100) : 0;

  return (
    <aside className="detail" style={{ ["--dim" as string]: color } as CSSProperties}>
      <button className="detail-close" onClick={onClose} aria-label="Close">×</button>

      {loading && <p className="muted small">Loading…</p>}

      {detail && (
        <>
          <div className="d-kicker">
            <span className="d-kdot" />
            <span>{detail.dimension.replace(/_/g, " ")}</span>
            {detail.status !== "active" && <em>· {statusWord(detail)}</em>}
          </div>

          <h2 className="d-value">{detail.value}</h2>

          <div className="d-sub">
            <span className="d-key">{detail.key.replace(/_/g, " ")}</span>
            <span className="d-since">
              {detail.status === "active"
                ? `since ${day(detail.valid_from)}`
                : `${day(detail.valid_from)} – ${day(detail.valid_to)}`}
            </span>
          </div>

          {detail.status !== "active" && onRestore && (
            <div className="d-restore">
              <span>This was marked {statusWord(detail)}.</span>
              <button onClick={() => onRestore(detail.id)}>↩ Restore</button>
            </div>
          )}

          <div className="d-conf" title={`raw ${Math.round(detail.confidence_raw * 100)}%`}>
            <div className="d-conf-head">
              <span className="d-conf-num">{conf}%</span>
              <span className="d-conf-cap">confidence</span>
            </div>
            <div className="d-conf-bar">
              <div className="d-conf-fill" style={{ width: `${conf}%` }} />
            </div>
          </div>

          {detail.attributes.filter((a) => a.status === "active").length > 0 && (
            <section className="d-section">
              <div className="d-label">details</div>
              <ul className="d-attrs">
                {detail.attributes
                  .filter((a) => a.status === "active")
                  .map((a) => (
                    <li key={a.id}>
                      <span className="d-attr-name">{a.name.replace(/_/g, " ")}</span>
                      <span className="d-attr-val">
                        {a.value}
                        {a.unit ? ` ${a.unit}` : ""}
                      </span>
                    </li>
                  ))}
              </ul>
            </section>
          )}

          {detail.evidence.length > 0 && (
            <section className="d-section">
              <div className="d-label">grounded in</div>
              {detail.evidence.map((e) => (
                <blockquote key={e.id} className="d-quote">
                  {e.quote}
                  <cite>
                    {e.session_title ? e.session_title : "this chat"} · {day(e.created_at)}
                  </cite>
                </blockquote>
              ))}
            </section>
          )}

          {detail.history.length > 1 && (
            <section className="d-section">
              <div className="d-label">how it changed</div>
              <ol className="d-timeline">
                {detail.history.map((h) => (
                  <li key={h.id} className={h.id === detail.id ? "current" : ""}>
                    <span className={`d-tdot status-${h.status}`} />
                    <div>
                      <div className="d-tval">{h.value}</div>
                      <div className="d-tmeta">
                        {transition(h)} · {day(h.valid_from)}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          )}
        </>
      )}
    </aside>
  );
}

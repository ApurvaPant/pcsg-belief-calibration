import { useState } from "react";
import type { Session, User } from "../types";
import type { Theme } from "../useTheme";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDeleteSession: (id: string, purgeMemory: boolean) => void | Promise<void>;
  busy: boolean;
  user: User;
  onLogout: () => void;
  theme: Theme;
  onToggleTheme: () => void;
}

function when(ts: string): string {
  const d = new Date(ts);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function Sidebar({
  sessions, activeId, onSelect, onNewChat, onDeleteSession, busy, user, onLogout, theme, onToggleTheme,
}: Props) {
  const name = user.username || user.label || "You";
  const [confirm, setConfirm] = useState<Session | null>(null);
  const [purge, setPurge] = useState(false);
  const [deleting, setDeleting] = useState(false);

  async function doDelete() {
    if (!confirm) return;
    setDeleting(true);
    try {
      await onDeleteSession(confirm.id, purge);
      setConfirm(null);
      setPurge(false);
    } finally {
      setDeleting(false);
    }
  }

  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={onNewChat} disabled={busy}>
        <span className="plus">+</span> New chat
      </button>

      <div className="sidebar-label eyebrow">Chats</div>
      <nav className="session-list">
        {sessions.length === 0 && <p className="muted small empty-hint">No chats yet.</p>}
        {sessions.map((s) => (
          <div key={s.id} className={`session-row ${s.id === activeId ? "active" : ""}`}>
            <button
              className={`session-item ${s.id === activeId ? "active" : ""}`}
              onClick={() => onSelect(s.id)}
            >
              <span className="session-title">{s.title || "New chat"}</span>
              <span className="session-when">{when(s.created_at)}</span>
            </button>
            <button
              className="session-del"
              title="Delete chat"
              aria-label="Delete chat"
              onClick={(e) => { e.stopPropagation(); setPurge(false); setConfirm(s); }}
            >
              ×
            </button>
          </div>
        ))}
      </nav>

      <div className="sidebar-foot">
        <span className="avatar">{name.slice(0, 1)}</span>
        <span className="user-name" title={name}>{name}</span>
        <button
          className="icon-ghost"
          onClick={onToggleTheme}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? "☾" : "☀"}
        </button>
        <button className="icon-ghost" onClick={onLogout} title="Log out" aria-label="Log out">
          ⏻
        </button>
      </div>

      {confirm && (
        <div className="confirm-overlay" onClick={() => !deleting && setConfirm(null)}>
          <div className="confirm-card" onClick={(e) => e.stopPropagation()}>
            <h3 className="confirm-title">Delete chat?</h3>
            <p className="confirm-text">
              “{confirm.title || "New chat"}” will be removed. This can’t be undone.
            </p>
            <label className="confirm-check">
              <input
                type="checkbox"
                checked={purge}
                onChange={(e) => setPurge(e.target.checked)}
              />
              <span>
                Also delete the memory &amp; graph nodes learned from this chat
                <em>Facts also learned from other chats are kept.</em>
              </span>
            </label>
            <div className="confirm-actions">
              <button className="btn-ghost" onClick={() => setConfirm(null)} disabled={deleting}>
                Cancel
              </button>
              <button className="btn-danger" onClick={doDelete} disabled={deleting}>
                {deleting ? "Deleting…" : purge ? "Delete chat & memory" : "Delete chat"}
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  );
}

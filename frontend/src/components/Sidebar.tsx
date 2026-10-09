import { useEffect, useRef, useState } from "react";
import type { Session, User } from "../types";
import type { Theme } from "../useTheme";
import { IconChart, IconMemory, IconPencil, IconTrash } from "./icons";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
  onDeleteSession: (id: string, purgeMemory: boolean) => void | Promise<void>;
  onRenameSession: (id: string, title: string) => void | Promise<void>;
  onOpenMemory: () => void;
  memoryOpen: boolean;
  onOpenResults: () => void;
  resultsOpen: boolean;
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
  sessions, activeId, onSelect, onNewChat, onDeleteSession, onRenameSession,
  onOpenMemory, memoryOpen, onOpenResults, resultsOpen, busy, user, onLogout, theme, onToggleTheme,
}: Props) {
  const name = user.username || user.label || "You";
  const [confirm, setConfirm] = useState<Session | null>(null);
  const [purge, setPurge] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const renameRef = useRef<HTMLInputElement>(null);

  // Dismiss an open kebab menu on any outside click / Escape.
  useEffect(() => {
    if (!menuFor) return;
    const close = () => setMenuFor(null);
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMenuFor(null);
    window.addEventListener("click", close);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("click", close);
      window.removeEventListener("keydown", onKey);
    };
  }, [menuFor]);

  useEffect(() => {
    if (renaming) renameRef.current?.select();
  }, [renaming]);

  function startRename(s: Session) {
    setMenuFor(null);
    setRenaming(s.id);
    setDraft(s.title || "");
  }

  async function commitRename(id: string) {
    const title = draft.trim();
    setRenaming(null);
    if (title && title !== (sessions.find((s) => s.id === id)?.title || "")) {
      await onRenameSession(id, title);
    }
  }

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

      <button
        className={`nav-item ${memoryOpen ? "active" : ""}`}
        onClick={onOpenMemory}
        title="See and manage what the system knows about you"
      >
        <span className="nav-ico"><IconMemory size={17} /></span>
        <span>Your memory</span>
      </button>

      <button
        className={`nav-item ${resultsOpen ? "active" : ""}`}
        onClick={onOpenResults}
        title="Experiment results — read-only charts from results/"
      >
        <span className="nav-ico"><IconChart size={17} /></span>
        <span>Results</span>
      </button>

      <div className="sidebar-label eyebrow">Chats</div>
      <nav className="session-list">
        {sessions.length === 0 && <p className="muted small empty-hint">No chats yet.</p>}
        {sessions.map((s) => (
          <div key={s.id} className={`session-row ${s.id === activeId ? "active" : ""}`}>
            {renaming === s.id ? (
              <input
                ref={renameRef}
                className="session-rename"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onClick={(e) => e.stopPropagation()}
                onBlur={() => commitRename(s.id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); commitRename(s.id); }
                  if (e.key === "Escape") { e.preventDefault(); setRenaming(null); }
                }}
              />
            ) : (
              <button
                className={`session-item ${s.id === activeId ? "active" : ""}`}
                onClick={() => onSelect(s.id)}
                onDoubleClick={() => startRename(s)}
              >
                <span className="session-title">{s.title || "New chat"}</span>
                <span className="session-when">{when(s.created_at)}</span>
              </button>
            )}

            <button
              className={`session-kebab ${menuFor === s.id ? "open" : ""}`}
              title="Chat options"
              aria-label="Chat options"
              aria-haspopup="menu"
              onClick={(e) => {
                e.stopPropagation();
                setMenuFor(menuFor === s.id ? null : s.id);
              }}
            >
              ⋯
            </button>

            {menuFor === s.id && (
              <div className="row-menu" role="menu" onClick={(e) => e.stopPropagation()}>
                <button className="row-menu-item" role="menuitem" onClick={() => startRename(s)}>
                  <IconPencil size={15} /> Rename
                </button>
                <button
                  className="row-menu-item danger"
                  role="menuitem"
                  onClick={() => { setMenuFor(null); setPurge(false); setConfirm(s); }}
                >
                  <IconTrash size={15} /> Delete
                </button>
              </div>
            )}
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

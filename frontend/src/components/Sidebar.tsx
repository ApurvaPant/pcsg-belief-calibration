import type { Session, User } from "../types";
import type { Theme } from "../useTheme";

interface Props {
  sessions: Session[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNewChat: () => void;
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
  sessions, activeId, onSelect, onNewChat, busy, user, onLogout, theme, onToggleTheme,
}: Props) {
  const name = user.username || user.label || "You";
  return (
    <aside className="sidebar">
      <button className="new-chat" onClick={onNewChat} disabled={busy}>
        <span className="plus">+</span> New chat
      </button>

      <div className="sidebar-label eyebrow">Chats</div>
      <nav className="session-list">
        {sessions.length === 0 && <p className="muted small empty-hint">No chats yet.</p>}
        {sessions.map((s) => (
          <button
            key={s.id}
            className={`session-item ${s.id === activeId ? "active" : ""}`}
            onClick={() => onSelect(s.id)}
          >
            <span className="session-title">{s.title || "New chat"}</span>
            <span className="session-when">{when(s.created_at)}</span>
          </button>
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
    </aside>
  );
}

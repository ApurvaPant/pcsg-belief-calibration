import { useCallback, useEffect, useState } from "react";
import ChatPanel from "./components/ChatPanel";
import GraphPanel from "./components/GraphPanel";
import NodeDetailView from "./components/NodeDetail";
import Sidebar from "./components/Sidebar";
import Login from "./components/Login";
import {
  deleteSession as apiDeleteSession,
  describeApiError,
  fetchNode,
  fetchSessionMessages,
  fetchSessions,
  fetchUserGraph,
  getAuthToken,
  logout as apiLogout,
  me,
  sendChat,
  setAuthToken,
  setUnauthorizedHandler,
} from "./api";
import type { ChatMessage, GraphPayload, NodeDetail, Session, User } from "./types";
import { useTheme } from "./useTheme";

export default function App() {
  const [theme, toggleTheme] = useTheme();
  const [user, setUser] = useState<User | null>(null);
  const [booting, setBooting] = useState(true);

  // Any 401 (e.g. the account was reset away) drops us back to login.
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  // Validate an existing token on load.
  useEffect(() => {
    (async () => {
      if (!getAuthToken()) {
        setBooting(false);
        return;
      }
      try {
        setUser(await me());
      } catch {
        setAuthToken(null);
      } finally {
        setBooting(false);
      }
    })();
  }, []);

  if (booting) return <div className="auth-screen" />;
  if (!user) return <Login onAuth={setUser} />;
  return (
    <Workspace
      user={user}
      theme={theme}
      onToggleTheme={toggleTheme}
      onLogout={async () => {
        await apiLogout();
        setUser(null);
      }}
    />
  );
}

interface WorkspaceProps {
  user: User;
  theme: ReturnType<typeof useTheme>[0];
  onToggleTheme: () => void;
  onLogout: () => void;
}

function Workspace({ user, theme, onToggleTheme, onLogout }: WorkspaceProps) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [graph, setGraph] = useState<GraphPayload | null>(null);
  const [busy, setBusy] = useState(false);
  const [showGraph, setShowGraph] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<NodeDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refreshGraph = useCallback(async () => {
    try {
      setGraph(await fetchUserGraph());
    } catch (e) {
      setError(describeApiError(e));
    }
  }, []);

  // Bootstrap sessions + graph.
  useEffect(() => {
    (async () => {
      try {
        const sess = await fetchSessions();
        setSessions(sess);
        setActiveSession(sess[0]?.id ?? null);
        await refreshGraph();
      } catch (e) {
        setError(describeApiError(e));
      }
    })();
  }, [refreshGraph]);

  // Load transcript for the active session.
  useEffect(() => {
    if (!activeSession) {
      setMessages([]);
      return;
    }
    (async () => {
      try {
        const stored = await fetchSessionMessages(activeSession);
        setMessages(
          stored.map((m) => ({
            role: m.role,
            content: m.content,
            salient: m.role === "user" ? m.salient : undefined,
            salience_score: m.salience_score ?? undefined,
          })),
        );
      } catch (e) {
        setError(describeApiError(e));
      }
    })();
  }, [activeSession]);

  const handleNewChat = useCallback(() => {
    setActiveSession(null);
    setMessages([]);
    setSelectedId(null);
    setDetail(null);
  }, []);

  const handleSend = useCallback(
    async (text: string) => {
      setBusy(true);
      setError(null);
      setMessages((m) => [...m, { role: "user", content: text }]);
      try {
        const res = await sendChat(text, activeSession);
        if (!activeSession || !sessions.some((s) => s.id === res.session_id)) {
          setActiveSession(res.session_id);
          setSessions(await fetchSessions());
        }
        setMessages((m) => {
          const copy = [...m];
          for (let i = copy.length - 1; i >= 0; i--) {
            if (copy[i].role === "user" && copy[i].salient === undefined) {
              copy[i] = { ...copy[i], salient: res.salient, salience_score: res.salience_score, changes: res.changes };
              break;
            }
          }
          copy.push({ role: "assistant", content: res.reply });
          return copy;
        });
        await refreshGraph();
      } catch (e) {
        const msg = describeApiError(e);
        setError(msg);
        setMessages((m) => [...m, { role: "assistant", content: `⚠️ ${msg}` }]);
      } finally {
        setBusy(false);
      }
    },
    [activeSession, sessions, refreshGraph],
  );

  const handleDeleteSession = useCallback(
    async (id: string, purgeMemory: boolean) => {
      setError(null);
      try {
        await apiDeleteSession(id, purgeMemory);
        const sess = await fetchSessions();
        setSessions(sess);
        if (activeSession === id) {
          setActiveSession(sess[0]?.id ?? null);
          setMessages([]);
          setSelectedId(null);
          setDetail(null);
        }
        if (purgeMemory) await refreshGraph();
      } catch (e) {
        setError(describeApiError(e));
      }
    },
    [activeSession, refreshGraph],
  );

  const handleSelectNode = useCallback(async (nodeId: string) => {
    setSelectedId(nodeId);
    setDetailLoading(true);
    try {
      setDetail(await fetchNode(nodeId));
    } catch (e) {
      setError(describeApiError(e));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // Keep the open inspector fresh after the graph mutates.
  useEffect(() => {
    if (selectedId && detail) fetchNode(selectedId).then(setDetail).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph]);

  const activeTitle = sessions.find((s) => s.id === activeSession)?.title || "New chat";
  const userInitial = (user.username || "U").slice(0, 1).toUpperCase();

  return (
    <div className="app">
      <Sidebar
        sessions={sessions}
        activeId={activeSession}
        onSelect={(id) => { setActiveSession(id); setSelectedId(null); setDetail(null); }}
        onNewChat={handleNewChat}
        onDeleteSession={handleDeleteSession}
        busy={busy}
        user={user}
        onLogout={onLogout}
        theme={theme}
        onToggleTheme={onToggleTheme}
      />

      <div className="main">
        <div className="chat-header">
          <span className="title">{activeTitle}</span>
          <div className="actions">
            <button
              className={`pill-btn ${showGraph ? "on" : ""}`}
              onClick={() => {
                setShowGraph((v) => !v);
                if (showGraph) { setSelectedId(null); setDetail(null); }
              }}
            >
              ◈ {showGraph ? "Hide graph" : "Show graph"}
            </button>
          </div>
        </div>
        {error && <div className="error-bar">{error}</div>}
        <ChatPanel messages={messages} onSend={handleSend} busy={busy} userInitial={userInitial} />
      </div>

      {showGraph && (
        <section className="graph-pane">
          <GraphPanel graph={graph} onSelectNode={handleSelectNode} selectedId={selectedId} theme={theme} />
        </section>
      )}

      {showGraph && (detail || detailLoading) && (
        <NodeDetailView
          detail={detail}
          loading={detailLoading}
          onClose={() => { setDetail(null); setSelectedId(null); }}
        />
      )}
    </div>
  );
}

import { useEffect, useRef, useState } from "react";
import type { ChatMessage } from "../types";

interface Props {
  messages: ChatMessage[];
  onSend: (text: string) => void;
  busy: boolean;
  userInitial: string;
}

export default function ChatPanel({ messages, onSend, busy, userInitial }: Props) {
  const [text, setText] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Keep the composer focused after a send completes so messages can be fired
  // back-to-back without clicking back into the box (ChatGPT/Claude behaviour).
  useEffect(() => {
    if (!busy) taRef.current?.focus();
  }, [busy]);

  function autosize() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 180) + "px";
  }

  function submit() {
    const value = text.trim();
    if (!value || busy) return;
    onSend(value);
    setText("");
    requestAnimationFrame(() => {
      if (taRef.current) {
        taRef.current.style.height = "auto";
        taRef.current.focus();
      }
    });
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="chat">
      <div className="messages">
        <div className="messages-inner">
          {messages.length === 0 && (
            <div className="intro">
              Tell me about yourself — a preference, goal, project, or habit.
              <br />
              I’ll build a living model of you in the graph.
            </div>
          )}
          {messages.map((m, i) => (
            <div key={i} className={`msg msg-${m.role}`}>
              <div className="msg-avatar">{m.role === "user" ? userInitial : "◈"}</div>
              <div className="msg-body">
                <div className="msg-role">{m.role === "user" ? "You" : "Assistant"}</div>
                <div className="bubble">{m.content}</div>
                {m.role === "user" && m.salience_score !== undefined && (() => {
                  const isQuery = m.changes?.some((c) => c.action === "retrieval_query");
                  return (
                    <div className="msg-meta">
                      salience {m.salience_score.toFixed(2)}{" "}
                      {isQuery ? (
                        <span className="tag tag-retrieval_query">retrieval query</span>
                      ) : m.salient ? (
                        <span className="tag tag-salient">extracted</span>
                      ) : (
                        <span className="tag tag-drop">gated out</span>
                      )}
                      {m.changes?.filter((c) => c.action !== "retrieval_query").map((c, j) => {
                        const slot = c.key ? `${c.dimension || "edge"}/${c.key}` : c.dimension;
                        const note = c.reason_code
                          ? ` (${c.reason_code})`
                          : c.action === "reinforced" && c.similarity != null
                          ? ` (sim ${c.similarity.toFixed(2)})`
                          : "";
                        return (
                          <span key={j} className={`tag tag-${c.action}`}>
                            {c.action}: {slot}
                            {note && <em className="tag-reason">{note}</em>}
                          </span>
                        );
                      })}
                    </div>
                  );
                })()}
              </div>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>

      <div className="composer-wrap">
        <div className="composer">
          <textarea
            ref={taRef}
            value={text}
            rows={1}
            onChange={(e) => {
              setText(e.target.value);
              autosize();
            }}
            onKeyDown={onKeyDown}
            placeholder="Message…"
            autoFocus
          />
          <button className="send-btn" onClick={submit} disabled={busy || !text.trim()} aria-label="Send">
            {busy ? "…" : "↑"}
          </button>
        </div>
        <div className="composer-hint">Enter to send · Shift+Enter for a new line</div>
      </div>
    </div>
  );
}

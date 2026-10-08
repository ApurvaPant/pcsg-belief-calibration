import type {
  ChatResponse,
  GraphPayload,
  MemoryPayload,
  NodeDetail,
  Session,
  StoredMessage,
  User,
} from "./types";

// Target the backend over IPv4 explicitly. The dev page is served on IPv6
// loopback ([::1]), so a "localhost" host would make the browser call the API
// over IPv6 (::1:8000) where nothing listens; 127.0.0.1 forces the IPv4 path
// that the backend actually binds. Override with VITE_API_BASE if the API is
// elsewhere (e.g. a LAN IP or forwarded host).
const BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:8000";
const TOKEN_KEY = "pcsg_token";

let token: string | null = localStorage.getItem(TOKEN_KEY);

export function setAuthToken(t: string | null) {
  token = t;
  if (t) localStorage.setItem(TOKEN_KEY, t);
  else localStorage.removeItem(TOKEN_KEY);
}

export function getAuthToken(): string | null {
  return token;
}

// Invoked when the server rejects our token (e.g. the account was removed).
let onUnauthorized: (() => void) | null = null;
export function setUnauthorizedHandler(fn: (() => void) | null) {
  onUnauthorized = fn;
}

function headers(extra: Record<string, string> = {}): Record<string, string> {
  const h: Record<string, string> = { ...extra };
  if (token) h.Authorization = `Bearer ${token}`;
  return h;
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = `${res.status}: ${body.detail}`;
    } catch {
      /* ignore */
    }
    // Stale / invalid token: drop it and bounce back to login.
    if (res.status === 401) {
      setAuthToken(null);
      onUnauthorized?.();
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export const API_BASE = BASE;

export function describeApiError(e: unknown): string {
  if (e instanceof TypeError) {
    return `Cannot reach the backend at ${BASE} — it looks down (connection refused) or is blocking this origin (CORS). Start it: uvicorn app.main:app --port 8000`;
  }
  if (e instanceof Error) {
    if (/^5\d\d/.test(e.message)) return `Backend error — ${e.message}`;
    return e.message;
  }
  return String(e);
}

// ── Auth ──────────────────────────────────────────────────────────────── #
interface AuthResult {
  token: string;
  user: User;
}

export async function register(username: string, password: string): Promise<AuthResult> {
  const res = await fetch(`${BASE}/auth/register`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ username, password }),
  });
  const out = await json<AuthResult>(res);
  setAuthToken(out.token);
  return out;
}

export async function login(username: string, password: string): Promise<AuthResult> {
  const res = await fetch(`${BASE}/auth/login`, {
    method: "POST",
    headers: headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ username, password }),
  });
  const out = await json<AuthResult>(res);
  setAuthToken(out.token);
  return out;
}

export async function me(): Promise<User> {
  return json<User>(await fetch(`${BASE}/auth/me`, { headers: headers() }));
}

export async function logout(): Promise<void> {
  try {
    await fetch(`${BASE}/auth/logout`, { method: "POST", headers: headers() });
  } finally {
    setAuthToken(null);
  }
}

// ── App data ──────────────────────────────────────────────────────────── #
export async function fetchSessions(): Promise<Session[]> {
  return json<Session[]>(await fetch(`${BASE}/me/sessions`, { headers: headers() }));
}

export async function createSession(title?: string): Promise<Session> {
  return json<Session>(
    await fetch(`${BASE}/me/sessions`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ title: title ?? null }),
    }),
  );
}

export interface DeleteSessionResult {
  deleted: boolean;
  purged_memory: boolean;
  removed_nodes: number;
  removed_attributes: number;
}

export async function deleteSession(
  sessionId: string,
  purgeMemory: boolean,
): Promise<DeleteSessionResult> {
  return json<DeleteSessionResult>(
    await fetch(`${BASE}/sessions/${sessionId}?purge_memory=${purgeMemory}`, {
      method: "DELETE",
      headers: headers(),
    }),
  );
}

export async function renameSession(sessionId: string, title: string): Promise<Session> {
  return json<Session>(
    await fetch(`${BASE}/sessions/${sessionId}`, {
      method: "PATCH",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ title }),
    }),
  );
}

export async function fetchSessionMessages(sessionId: string): Promise<StoredMessage[]> {
  return json<StoredMessage[]>(
    await fetch(`${BASE}/sessions/${sessionId}/messages`, { headers: headers() }),
  );
}

export async function sendChat(message: string, sessionId: string | null): Promise<ChatResponse> {
  return json<ChatResponse>(
    await fetch(`${BASE}/chat`, {
      method: "POST",
      headers: headers({ "Content-Type": "application/json" }),
      body: JSON.stringify({ message, session_id: sessionId }),
    }),
  );
}

export async function fetchUserGraph(): Promise<GraphPayload> {
  return json<GraphPayload>(await fetch(`${BASE}/me/graph`, { headers: headers() }));
}

export async function fetchNode(nodeId: string): Promise<NodeDetail> {
  return json<NodeDetail>(await fetch(`${BASE}/node/${nodeId}`, { headers: headers() }));
}

export async function restoreNode(nodeId: string): Promise<{ restored: boolean }> {
  return json<{ restored: boolean }>(
    await fetch(`${BASE}/nodes/${nodeId}/restore`, { method: "POST", headers: headers() }),
  );
}

export async function fetchMemory(): Promise<MemoryPayload> {
  return json<MemoryPayload>(await fetch(`${BASE}/me/memory`, { headers: headers() }));
}

export async function forgetNode(nodeId: string): Promise<{ forgotten: boolean; removed_nodes: number }> {
  return json<{ forgotten: boolean; removed_nodes: number }>(
    await fetch(`${BASE}/nodes/${nodeId}`, { method: "DELETE", headers: headers() }),
  );
}

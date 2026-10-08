export type NodeStatus = "active" | "superseded" | "deleted";

export interface UserNode {
  id: string;
  user_id: string;
  dimension: string;
  key: string;
  value: string;
  confidence_raw: number;
  confidence_calibrated: number;
  valid_from: string;
  valid_to: string | null;
  status: NodeStatus;
  status_detail: string;
  previous_node_id: string | null;
  created_at: string;
  label: string;
}

export interface EvidenceItem {
  id: string;
  session_id: string | null;
  session_title: string | null;
  message_id: string;
  quote: string;
  created_at: string;
}

export interface NodeAttribute {
  id: string;
  name: string;
  value: string;
  unit: string | null;
  confidence_calibrated: number | null;
  status: string;
  valid_from: string;
  valid_to: string | null;
}

export interface NodeDetail extends UserNode {
  evidence: EvidenceItem[];
  history: UserNode[];
  attributes: NodeAttribute[];
}

export interface GraphElement<T> {
  data: T;
}

export interface GraphPayload {
  user_id: string;
  nodes: GraphElement<UserNode>[];
  edges: GraphElement<{ id: string; source: string; target: string; relation: string }>[];
}

export interface TurnChange {
  action: "created" | "superseded" | "reinforced" | "ended" | "ignored" | "refined" | "linked" | "retrieval_query" | "none";
  node_id: string | null;
  dimension: string;
  key: string;
  value: string;
  previous_node_id: string | null;
  reason_code?: string | null;
  matched_label?: string | null;
  similarity?: number | null;
}

export interface ChatResponse {
  session_id: string;
  user_id: string;
  reply: string;
  salient: boolean;
  salience_score: number;
  changes: TurnChange[];
  latency_ms: number;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  salient?: boolean;
  salience_score?: number;
  changes?: TurnChange[];
}

export interface User {
  id: string;
  username: string | null;
  label: string | null;
  created_at: string;
}

export interface Session {
  id: string;
  user_id: string;
  title: string | null;
  created_at: string;
  closed_at: string | null;
}

export interface MemoryNode extends UserNode {
  attributes: { name: string; value: string; unit: string | null }[];
  quote: string | null;
  evidence_count: number;
}

export interface MemoryPayload {
  user_id: string;
  nodes: MemoryNode[];
  counts: { total: number; active: number; past: number };
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  salience_score: number | null;
  salient: boolean;
}

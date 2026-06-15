/**
 * Control plane shared types.
 *
 * Mirrors the EventRecord / SessionRecord / ApprovalRecord schemas served by
 * `gateway/control_plane/routes/*.py`. Keep field names in sync — UI relies on
 * exact key names (`runtime_kind`, `action_kind`, `decided_by` …).
 */

export type SessionStatus =
  | "created"
  | "running"
  | "waiting"
  | "completed"
  | "failed"
  | "cancelled"
  | "stopped";

export type SessionRecord = {
  id: string;
  runtime_kind: string;
  model: string;
  repo_path?: string | null;
  workspace_id?: string | null;
  status: string;
  started_at: string;
  ended_at?: string | null;
  metadata?: Record<string, unknown>;
};

export type SessionListResponse = {
  sessions: SessionRecord[];
  total: number;
  limit: number;
  offset: number;
};

export type EventRecord = {
  id: number;
  session_id: string;
  turn_id?: string | null;
  type: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type EventsResponse = {
  events: EventRecord[];
};

export type ApprovalDecision = "approved" | "denied" | "pending";

export type ApprovalRecord = {
  id: string;
  session_id: string;
  action_kind: string;
  action_payload: Record<string, unknown>;
  decision: ApprovalDecision;
  decided_by?: string | null;
  decided_at?: string | null;
  created_at?: string;
};

export type ApprovalsResponse = ApprovalRecord[];

export type WsStatus = "idle" | "connecting" | "open" | "polling" | "closed";

/** Status pill colours (CSS). Falls back to neutral grey for unknown states. */
export const STATUS_COLORS: Record<string, string> = {
  created: "#6b7280",
  running: "#10b981",
  waiting: "#f59e0b",
  completed: "#3b82f6",
  failed: "#ef4444",
  cancelled: "#9ca3af",
  stopped: "#9ca3af",
};

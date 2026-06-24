export type RunStatus =
  | "queued"
  | "running"
  | "coding"
  | "dispatching"
  | "reviewing_passed"
  | "retrying"
  | "test_failing"
  | "auditing"
  | "done"
  | "failed";

export interface AgentStep {
  task_id: string;
  agent: string;
  status: "success" | "failed" | "skipped" | "running";
  summary: string;
  files_written: string[];
  error?: string;
}

export interface Run {
  id: string;
  prompt: string;
  status: RunStatus;
  created_at: string;
  completed_at?: string;
  error?: string;
  steps: AgentStep[];
}

export interface NodeUpdate {
  event: "run_started" | "node_update" | "run_finished";
  node?: string;
  status?: string;
  current_task_id?: string;
  run_id?: string;
  error?: string;
}

export const AGENT_ORDER = [
  "architect",
  "dispatcher",
  "coder",
  "opa_gate",
  "reviewer",
  "tester",
  "auditor",
] as const;

export const AGENT_LABELS: Record<string, string> = {
  architect: "Architect",
  dispatcher: "Dispatcher",
  coder: "Coder",
  opa_gate: "OPA Gate",
  reviewer: "Reviewer",
  tester: "Tester",
  auditor: "Auditor",
};

export const STATUS_COLOR: Record<string, string> = {
  done: "var(--success)",
  success: "var(--success)",
  failed: "var(--error)",
  running: "var(--running)",
  coding: "var(--running)",
  retrying: "var(--warning)",
  test_failing: "var(--warning)",
  auditing: "var(--accent)",
  queued: "var(--text-muted)",
  skipped: "var(--text-muted)",
};

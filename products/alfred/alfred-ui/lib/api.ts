const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// --------------------------------------------------------------------------- #
// Runs
// --------------------------------------------------------------------------- #

export async function createRun(
  prompt: string,
  projectId?: string | null
): Promise<{ id: string }> {
  const res = await fetch(`${API}/api/v1/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt, project_id: projectId ?? null }),
  });
  if (!res.ok) throw new Error(`Error ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function getRuns(projectId?: string | null): Promise<
  { id: string; status: string; prompt: string; created_at: string }[]
> {
  const params = new URLSearchParams({ limit: "50", offset: "0" });
  if (projectId) params.set("project_id", projectId);
  const res = await fetch(`${API}/api/v1/runs?${params}`);
  if (!res.ok) return [];
  const data = await res.json();
  // Backend devuelve { runs: [...], total, limit, offset }
  return Array.isArray(data) ? data : (data.runs ?? []);
}

export async function getRun(runId: string): Promise<{
  id: string;
  prompt: string;
  status: string;
  current_agent: string | null;
  plan: unknown;
  result: unknown;
  error: string | null;
  created_at: string;
  completed_at: string | null;
  project_id: string | null;
  duration_ms: number | null;
  tokens_used: number | null;
} | null> {
  const res = await fetch(`${API}/api/v1/runs/${runId}`);
  if (!res.ok) return null;
  return res.json();
}

// --------------------------------------------------------------------------- #
// Projects
// --------------------------------------------------------------------------- #

export interface Project {
  id: string;
  name: string;
  description: string | null;
  repo_path: string | null;
  acd_path: string | null;
  created_at: string;
}

export async function getProjects(): Promise<Project[]> {
  const res = await fetch(`${API}/api/v1/projects`);
  if (!res.ok) return [];
  const data = await res.json();
  return Array.isArray(data) ? data : (data.projects ?? []);
}

// --------------------------------------------------------------------------- #
// SSE
// --------------------------------------------------------------------------- #

export function subscribeToRun(
  runId: string,
  onUpdate: (data: import("./types").NodeUpdate) => void,
  onDone: () => void
): () => void {
  const es = new EventSource(`${API}/api/v1/runs/${runId}/status`);

  es.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      onUpdate(data);
      if (data.event === "run_finished") {
        onDone();
        es.close();
      }
    } catch {}
  };

  es.onerror = () => es.close();

  return () => es.close();
}

export interface Step {
  id: string;
  agent_name: string;
  status: string;
  output: { task_id: string; summary: string; files_written: string[]; error: string | null } | null;
  cost_usd: number | null;
  created_at: string;
}

export async function getRunSteps(runId: string): Promise<Step[]> {
  const res = await fetch(`${API}/api/v1/runs/${runId}/steps`);
  if (!res.ok) return [];
  return res.json();
}

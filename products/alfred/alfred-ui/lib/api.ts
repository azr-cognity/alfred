const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export async function createRun(prompt: string): Promise<{ id: string }> {
  const res = await fetch(`${API}/api/v1/runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt }),
  });
  if (!res.ok) throw new Error(`Error ${res.status}: ${await res.text()}`);
  return res.json();
}

export async function getRuns(): Promise<{ id: string; status: string; prompt: string; created_at: string }[]> {
  const res = await fetch(`${API}/api/v1/runs`);
  if (!res.ok) return [];
  return res.json();
}

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

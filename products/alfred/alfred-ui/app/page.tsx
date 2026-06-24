"use client";
import { useState, useCallback, useEffect } from "react";
import { PromptBar } from "@/components/PromptBar";
import { PipelineView } from "@/components/PipelineView";
import { CodeViewer } from "@/components/CodeViewer";
import { RunSidebar } from "@/components/RunSidebar";
import { AgentStep, NodeUpdate, Run } from "@/lib/types";
import { createRun, getRuns, subscribeToRun } from "@/lib/api";

export default function Home() {
  const [runs, setRuns] = useState<Pick<Run, "id" | "prompt" | "status" | "created_at">[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Archivos escritos — tomar el último step con files_written para Monaco
  const lastFilesStep = [...steps]
    .reverse()
    .find(s => s.files_written?.length > 0 && s.agent === "coder");

  useEffect(() => {
    getRuns().then(setRuns).catch(() => {});
  }, []);

  const handleRun = useCallback(async (prompt: string) => {
    setLoading(true);
    setSteps([]);
    setActiveNode(null);
    setCurrentTaskId(null);
    setRunStatus("queued");
    setError(null);

    try {
      const { id } = await createRun(prompt);
      setActiveRunId(id);
      setRuns(prev => [{ id, prompt, status: "running", created_at: new Date().toISOString() }, ...prev]);

      const unsub = subscribeToRun(
        id,
        (update: NodeUpdate) => {
          if (update.node) setActiveNode(update.node);
          if (update.current_task_id) setCurrentTaskId(update.current_task_id);
          if (update.status) setRunStatus(update.status);

          if (update.event === "run_finished") {
            setActiveNode(null);
            setLoading(false);
            setRunStatus(update.status ?? null);
            if (update.error) setError(update.error);
            setRuns(prev => prev.map(r =>
              r.id === id ? { ...r, status: (update.status ?? "done") as Run["status"] } : r
            ));
          }
        },
        () => {
          setLoading(false);
          setActiveNode(null);
        }
      );

      return unsub;
    } catch (e: unknown) {
      setLoading(false);
      setError(e instanceof Error ? e.message : "Error al crear el run");
      setRunStatus("failed");
    }
  }, []);

  return (
    <div style={{ display: "flex", height: "100vh", background: "var(--bg)" }}>
      <RunSidebar
        runs={runs}
        activeId={activeRunId}
        onSelect={setActiveRunId}
      />

      {/* Main canvas */}
      <div style={{
        flex: 1,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{
          padding: "14px 24px",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          flexShrink: 0,
        }}>
          <span style={{ fontSize: 13, color: "var(--text-muted)" }}>
            {activeRunId ? (
              <>
                <span style={{ fontFamily: "monospace" }}>{activeRunId.slice(0, 8)}</span>
                {runStatus && (
                  <span style={{
                    marginLeft: 10,
                    fontSize: 11,
                    padding: "2px 8px",
                    borderRadius: 4,
                    background: "var(--surface-2)",
                    color: runStatus === "done" ? "var(--success)" : runStatus === "failed" ? "var(--error)" : "var(--running)",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}>
                    {runStatus}
                  </span>
                )}
              </>
            ) : "Nuevo run"}
          </span>
        </div>

        {/* Scrollable content */}
        <div style={{
          flex: 1,
          overflowY: "auto",
          padding: "20px 24px",
          display: "flex",
          flexDirection: "column",
          gap: 20,
        }}>
          <PromptBar onRun={handleRun} loading={loading} />

          {error && (
            <div style={{
              background: "rgba(248,113,113,0.08)",
              border: "1px solid rgba(248,113,113,0.3)",
              borderRadius: 8,
              padding: "10px 14px",
              fontSize: 13,
              color: "var(--error)",
            }}>
              {error}
            </div>
          )}

          {(steps.length > 0 || activeNode) && (
            <div style={{
              background: "var(--surface)",
              border: "1px solid var(--border)",
              borderRadius: 10,
              padding: "14px",
            }}>
              <p style={{
                fontSize: 11,
                color: "var(--text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.06em",
                fontWeight: 600,
                marginBottom: 12,
              }}>
                Pipeline
              </p>
              <PipelineView
                steps={steps}
                activeNode={activeNode}
                currentTaskId={currentTaskId}
              />
            </div>
          )}

          {lastFilesStep && (
            <CodeViewer
              code={"# Los archivos generados aparecen aquí\n# Conecta el backend para ver el código real"}
              language="python"
              filename={lastFilesStep.files_written[0]}
            />
          )}

          {/* Empty state */}
          {!activeRunId && steps.length === 0 && (
            <div style={{
              flex: 1,
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              justifyContent: "center",
              gap: 12,
              paddingTop: 60,
              color: "var(--text-muted)",
            }}>
              <div style={{
                width: 48, height: 48,
                borderRadius: 12,
                background: "var(--surface)",
                border: "1px solid var(--border)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 22,
              }}>
                ⚡
              </div>
              <p style={{ fontSize: 14, fontWeight: 500, color: "var(--text)" }}>
                Describe qué quieres construir
              </p>
              <p style={{ fontSize: 13, textAlign: "center", maxWidth: 340 }}>
                Alfred planificará, codificará, revisará y abrirá un PR automáticamente.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

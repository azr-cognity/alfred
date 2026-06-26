"use client";
import { useState, useCallback, useEffect } from "react";
import { PromptBar } from "@/components/PromptBar";
import { PipelineView } from "@/components/PipelineView";
import { CodeViewer } from "@/components/CodeViewer";
import { CostBadge } from "@/components/CostBadge";
import { RunSidebar } from "@/components/RunSidebar";
import { AgentStep, NodeUpdate, Run } from "@/lib/types";
import { createRun, getRun, getRuns, getProjects, Project, getRunSteps } from "@/lib/api";
import { subscribeToRun } from "@/lib/api";

export default function Home() {
  const [runs, setRuns] = useState<Pick<Run, "id" | "prompt" | "status" | "created_at">[]>([]);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [steps, setSteps] = useState<AgentStep[]>([]);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [currentTaskId, setCurrentTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [runStatus, setRunStatus] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runCost, setRunCost] = useState<number | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null);

  // Archivos escritos - codigo real desde result jsonb o placeholder
  const [codeContent, setCodeContent] = useState<string | null>(null);
  const [codeFilename, setCodeFilename] = useState<string | null>(null);

  const lastFilesStep = [...steps]
    .reverse()
    .find(s => s.files_written?.length > 0 && s.agent === "coder");

  // Cargar proyectos al montar
  useEffect(() => {
    getProjects().then(setProjects).catch(() => {});
  }, []);

  // Cargar runs al montar y cuando cambia el proyecto activo
  useEffect(() => {
    getRuns(activeProjectId).then(setRuns).catch(() => {});
  }, [activeProjectId]);

  // Cargar detalle de run al seleccionar uno del sidebar
  const handleSelectRun = useCallback(async (id: string) => {
    setActiveRunId(id);
    setSteps([]);
    setActiveNode(null);
    setCurrentTaskId(null);
    setError(null);
    setCodeContent(null);
    setCodeFilename(null);
    setRunCost(null);

    const run = await getRun(id).catch(() => null);
    if (!run) return;

    setRunStatus(run.status);
    setRunCost((run as any).cost_usd ?? null);

    // Cargar steps historicos del run
    const historical = await getRunSteps(id).catch(() => []);
    if (historical.length > 0) {
      setSteps(historical.map(s => ({
        task_id: s.output?.task_id ?? "",
        agent: s.agent_name,
        status: s.status as any,
        summary: s.output?.summary ?? "",
        files_written: s.output?.files_written ?? [],
        error: s.output?.error ?? undefined,
      })));
    }

    // Extraer archivos desde result jsonb si existen
    if (run.result && typeof run.result === "object") {
      const result = run.result as Record<string, unknown>;
      const files = result.files_written as Record<string, string> | undefined;
      if (files) {
        const firstPath = Object.keys(files)[0];
        if (firstPath) {
          setCodeFilename(firstPath);
          setCodeContent(files[firstPath]);
        }
      }
    }

    // Si el run sigue activo, suscribirse al SSE
    if (!["done", "failed"].includes(run.status)) {
      setLoading(true);
      subscribeToRun(
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
            // Recargar detalle para obtener archivos y costo
            getRun(id).then(updated => {
              if (!updated) return;
              setRunCost((updated as any).cost_usd ?? null);
              if (!updated.result) return;
              const result = updated.result as Record<string, unknown>;
              const files = result.files_written as Record<string, string> | undefined;
              if (files) {
                const firstPath = Object.keys(files)[0];
                if (firstPath) {
                  setCodeFilename(firstPath);
                  setCodeContent(files[firstPath]);
                }
              }
            }).catch(() => {});
          }
        },
        () => {
          setLoading(false);
          setActiveNode(null);
        }
      );
    }
  }, []);

  const handleNew = useCallback(() => {
    setActiveRunId(null);
    setSteps([]);
    setActiveNode(null);
    setCurrentTaskId(null);
    setRunStatus(null);
    setError(null);
    setCodeContent(null);
    setCodeFilename(null);
    setRunCost(null);
  }, []);

  const handleRun = useCallback(async (prompt: string) => {
    setLoading(true);
    setSteps([]);
    setActiveNode(null);
    setCurrentTaskId(null);
    setRunStatus("queued");
    setError(null);
    setCodeContent(null);
    setCodeFilename(null);
    setRunCost(null);

    try {
      const { id } = await createRun(prompt, activeProjectId);
      setActiveRunId(id);
      setRuns(prev => [
        { id, prompt, status: "running", created_at: new Date().toISOString() },
        ...prev,
      ]);

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
            // Recargar detalle para obtener archivos y costo
            getRun(id).then(updated => {
              if (!updated) return;
              setRunCost((updated as any).cost_usd ?? null);
              if (!updated.result) return;
              const result = updated.result as Record<string, unknown>;
              const files = result.files_written as Record<string, string> | undefined;
              if (files) {
                const firstPath = Object.keys(files)[0];
                if (firstPath) {
                  setCodeFilename(firstPath);
                  setCodeContent(files[firstPath]);
                }
              }
            }).catch(() => {});
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
  }, [activeProjectId]);

  const showCode = codeContent ?? (lastFilesStep
    ? "# Los archivos generados aparecen aqui\n# Ejecuta un run para ver el codigo real"
    : null);
  const showFilename = codeFilename ?? lastFilesStep?.files_written[0] ?? null;

  return (
    <div style={{ display: "flex", height: "100vh", background: "var(--bg)" }}>
      <RunSidebar
        runs={runs}
        activeId={activeRunId}
        onSelect={handleSelectRun}
        onNew={handleNew}
        projects={projects}
        activeProjectId={activeProjectId}
        onProjectChange={setActiveProjectId}
      />

      {/* Main canvas */}
      <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
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
                    color: runStatus === "done"
                      ? "var(--success)"
                      : runStatus === "failed"
                        ? "var(--error)"
                        : "var(--running)",
                    fontWeight: 600,
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                  }}>
                    {runStatus}
                  </span>
                )}
                {runCost !== null && (
                  <span style={{ marginLeft: 8 }}>
                    <CostBadge cost_usd={runCost} />
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

          {showCode && showFilename && (
            <CodeViewer
              code={showCode}
              language={showFilename.endsWith(".ts") || showFilename.endsWith(".tsx") ? "typescript" : "python"}
              filename={showFilename}
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
                Describe que quieres construir
              </p>
              <p style={{ fontSize: 13, textAlign: "center", maxWidth: 340 }}>
                Alfred planificara, codificara, revisara y abrira un PR automaticamente.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

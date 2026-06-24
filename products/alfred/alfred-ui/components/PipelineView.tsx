"use client";
import { AgentStep, AGENT_LABELS, AGENT_ORDER, STATUS_COLOR } from "@/lib/types";

interface Props {
  steps: AgentStep[];
  activeNode: string | null;
  currentTaskId: string | null;
}

function Spinner() {
  return (
    <span style={{
      display: "inline-block",
      width: 10, height: 10,
      border: "2px solid var(--border)",
      borderTopColor: "var(--running)",
      borderRadius: "50%",
      animation: "spin 0.7s linear infinite",
    }} />
  );
}

export function PipelineView({ steps, activeNode, currentTaskId }: Props) {
  const agentStatus = (agent: string): "idle" | "running" | "success" | "failed" | "skipped" => {
    if (activeNode === agent) return "running";
    const agentSteps = steps.filter(s => s.agent === agent);
    if (!agentSteps.length) return "idle";
    if (agentSteps.some(s => s.status === "failed")) return "failed";
    if (agentSteps.every(s => s.status === "success" || s.status === "skipped")) return "success";
    return "running";
  };

  const lastStepFor = (agent: string): AgentStep | null => {
    const ss = steps.filter(s => s.agent === agent);
    return ss.length ? ss[ss.length - 1] : null;
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      {AGENT_ORDER.map((agent, i) => {
        const status = agentStatus(agent);
        const step = lastStepFor(agent);
        const color = STATUS_COLOR[status === "running" ? "running" : step?.status ?? "queued"];

        return (
          <div key={agent}>
            <div style={{
              display: "flex",
              alignItems: "flex-start",
              gap: 12,
              padding: "10px 14px",
              background: status === "running" ? "var(--accent-dim)" : status !== "idle" ? "var(--surface)" : "transparent",
              borderRadius: 8,
              border: `1px solid ${status === "running" ? "var(--accent)" : status !== "idle" ? "var(--border)" : "transparent"}`,
              transition: "all 0.2s",
            }}>
              {/* Status indicator */}
              <div style={{ paddingTop: 3, flexShrink: 0 }}>
                {status === "running" ? (
                  <Spinner />
                ) : (
                  <div style={{
                    width: 10, height: 10,
                    borderRadius: "50%",
                    background: status === "idle" ? "var(--border)" : color,
                  }} />
                )}
              </div>

              {/* Content */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{
                    fontWeight: 600,
                    fontSize: 13,
                    color: status === "idle" ? "var(--text-muted)" : "var(--text)",
                  }}>
                    {AGENT_LABELS[agent] ?? agent}
                  </span>
                  {currentTaskId && status === "running" && (
                    <span style={{
                      fontSize: 11,
                      color: "var(--accent)",
                      background: "var(--accent-dim)",
                      padding: "1px 7px",
                      borderRadius: 4,
                    }}>
                      {currentTaskId}
                    </span>
                  )}
                </div>
                {step?.summary && (
                  <p style={{
                    fontSize: 12,
                    color: "var(--text-muted)",
                    marginTop: 2,
                    whiteSpace: "nowrap",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                  }}>
                    {step.summary}
                  </p>
                )}
                {step?.files_written?.length > 0 && (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 5 }}>
                    {step.files_written.map(f => (
                      <span key={f} style={{
                        fontSize: 11,
                        fontFamily: "monospace",
                        color: "var(--success)",
                        background: "rgba(52,211,153,0.08)",
                        padding: "1px 6px",
                        borderRadius: 3,
                      }}>
                        {f.split("/").pop()}
                      </span>
                    ))}
                  </div>
                )}
                {step?.error && status === "failed" && (
                  <p style={{ fontSize: 11, color: "var(--error)", marginTop: 3 }}>
                    {step.error.slice(0, 120)}
                  </p>
                )}
              </div>
            </div>
            {/* Connector line */}
            {i < AGENT_ORDER.length - 1 && (
              <div style={{
                width: 1, height: 8,
                background: "var(--border)",
                marginLeft: 19,
              }} />
            )}
          </div>
        );
      })}
    </div>
  );
}

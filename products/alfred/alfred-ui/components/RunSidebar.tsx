"use client";
import { Run, STATUS_COLOR } from "@/lib/types";
import { Project } from "@/lib/api";

interface Props {
  runs: Pick<Run, "id" | "prompt" | "status" | "created_at">[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  projects: Project[];
  activeProjectId: string | null;
  onProjectChange: (id: string | null) => void;
}

function timeAgo(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const m = Math.floor(diff / 60000);
  if (m < 1) return "ahora";
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h`;
  return `${Math.floor(h / 24)}d`;
}

export function RunSidebar({
  runs,
  activeId,
  onSelect,
  onNew,
  projects,
  activeProjectId,
  onProjectChange,
}: Props) {
  return (
    <div style={{
      width: 220,
      flexShrink: 0,
      borderRight: "1px solid var(--border)",
      display: "flex",
      flexDirection: "column",
      height: "100vh",
      overflowY: "auto",
    }}>
      {/* Logo + botón nuevo */}
      <div style={{
        padding: "14px 16px",
        borderBottom: "1px solid var(--border)",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
      }}>
        <span style={{
          fontSize: 17,
          fontWeight: 700,
          letterSpacing: "-0.02em",
          color: "var(--text)",
        }}>
          alfred<span style={{ color: "var(--accent)" }}>.</span>
        </span>
        <button
          onClick={onNew}
          title="Nuevo run"
          style={{
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 6,
            color: "var(--text-muted)",
            cursor: "pointer",
            fontSize: 16,
            lineHeight: 1,
            padding: "3px 7px",
          }}
        >
          +
        </button>
      </div>

      {/* Selector de proyecto */}
      {projects.length > 0 && (
        <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}>
          <p style={{
            fontSize: 11,
            color: "var(--text-muted)",
            textTransform: "uppercase",
            letterSpacing: "0.06em",
            fontWeight: 600,
            marginBottom: 6,
          }}>
            Proyecto
          </p>
          <select
            value={activeProjectId ?? ""}
            onChange={e => onProjectChange(e.target.value || null)}
            style={{
              width: "100%",
              background: "var(--surface-2)",
              border: "1px solid var(--border)",
              borderRadius: 6,
              color: "var(--text)",
              fontSize: 12,
              padding: "5px 8px",
              cursor: "pointer",
              outline: "none",
            }}
          >
            <option value="">Todos</option>
            {projects.map(p => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
        </div>
      )}

      {/* Runs list */}
      <div style={{ padding: "8px 0", flex: 1 }}>
        <p style={{
          fontSize: 11,
          color: "var(--text-muted)",
          padding: "4px 14px 8px",
          textTransform: "uppercase",
          letterSpacing: "0.06em",
          fontWeight: 600,
        }}>
          Runs recientes
        </p>
        {runs.length === 0 && (
          <p style={{ fontSize: 12, color: "var(--text-muted)", padding: "4px 14px" }}>
            Sin runs aún
          </p>
        )}
        {runs.map(run => (
          <button
            key={run.id}
            onClick={() => onSelect(run.id)}
            style={{
              width: "100%",
              textAlign: "left",
              background: activeId === run.id ? "var(--accent-dim)" : "transparent",
              border: "none",
              borderLeft: activeId === run.id ? "2px solid var(--accent)" : "2px solid transparent",
              padding: "8px 14px",
              cursor: "pointer",
              display: "flex",
              flexDirection: "column",
              gap: 3,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: 11, fontFamily: "monospace", color: "var(--text-muted)" }}>
                {run.id.slice(0, 8)}
              </span>
              <span style={{
                width: 6, height: 6,
                borderRadius: "50%",
                background: STATUS_COLOR[run.status] ?? "var(--text-muted)",
                flexShrink: 0,
              }} />
            </div>
            <p style={{
              fontSize: 12,
              color: "var(--text)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
              lineHeight: 1.4,
            }}>
              {run.prompt}
            </p>
            <span style={{ fontSize: 11, color: "var(--text-muted)" }}>
              {timeAgo(run.created_at)}
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}

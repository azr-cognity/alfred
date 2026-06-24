"use client";
import { useState, useRef } from "react";

interface Props {
  onRun: (prompt: string) => void;
  loading: boolean;
}

export function PromptBar({ onRun, loading }: Props) {
  const [prompt, setPrompt] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);

  const submit = () => {
    const p = prompt.trim();
    if (!p || loading) return;
    onRun(p);
    setPrompt("");
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submit();
  };

  return (
    <div style={{
      background: "var(--surface)",
      border: "1px solid var(--border)",
      borderRadius: 10,
      padding: "12px 14px",
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      <textarea
        ref={ref}
        value={prompt}
        onChange={e => setPrompt(e.target.value)}
        onKeyDown={onKey}
        placeholder="Describe qué quieres que Alfred construya..."
        rows={3}
        disabled={loading}
        style={{
          background: "transparent",
          border: "none",
          outline: "none",
          color: "var(--text)",
          fontSize: 14,
          lineHeight: 1.6,
          resize: "none",
          width: "100%",
          fontFamily: "inherit",
          opacity: loading ? 0.5 : 1,
        }}
      />
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ color: "var(--text-muted)", fontSize: 12 }}>
          ⌘ + Enter para ejecutar
        </span>
        <button
          onClick={submit}
          disabled={!prompt.trim() || loading}
          style={{
            background: loading ? "var(--surface-2)" : "var(--accent)",
            color: loading ? "var(--text-muted)" : "#fff",
            border: "none",
            borderRadius: 6,
            padding: "7px 18px",
            fontSize: 13,
            fontWeight: 600,
            cursor: loading ? "not-allowed" : "pointer",
            transition: "opacity 0.15s",
            letterSpacing: "0.01em",
          }}
        >
          {loading ? "Ejecutando..." : "Run"}
        </button>
      </div>
    </div>
  );
}

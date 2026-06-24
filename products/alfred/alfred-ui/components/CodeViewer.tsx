"use client";
import dynamic from "next/dynamic";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), { ssr: false });

interface Props {
  code: string;
  language?: string;
  filename?: string;
}

export function CodeViewer({ code, language = "python", filename }: Props) {
  if (!code) return null;

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 10,
      overflow: "hidden",
    }}>
      {filename && (
        <div style={{
          background: "var(--surface-2)",
          borderBottom: "1px solid var(--border)",
          padding: "7px 14px",
          fontSize: 12,
          fontFamily: "monospace",
          color: "var(--text-muted)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}>
          <span style={{
            width: 8, height: 8, borderRadius: "50%",
            background: "var(--success)", display: "inline-block",
          }} />
          {filename}
        </div>
      )}
      <MonacoEditor
        height="340px"
        language={language}
        value={code}
        theme="vs-dark"
        options={{
          readOnly: true,
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: "on",
          scrollBeyondLastLine: false,
          wordWrap: "on",
          renderLineHighlight: "none",
          folding: false,
          padding: { top: 12, bottom: 12 },
        }}
      />
    </div>
  );
}

interface CostBadgeProps {
  cost_usd: number | null | undefined;
}

export function CostBadge({ cost_usd }: CostBadgeProps) {
  if (cost_usd === null || cost_usd === undefined) return null;

  const label = cost_usd === 0 ? "local" : `$${cost_usd.toFixed(4)}`;

  return (
    <span style={{
      fontSize: 11,
      padding: "2px 8px",
      borderRadius: 4,
      background: "var(--surface-2)",
      color: cost_usd === 0 ? "var(--text-muted)" : "var(--accent)",
      fontWeight: 600,
      fontFamily: "monospace",
      letterSpacing: "0.02em",
    }}>
      {label}
    </span>
  );
}

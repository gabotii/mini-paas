import type { ReactNode } from "react";

interface MetricCardProps {
  label: string;
  value: string;
  hint?: string;
  icon?: ReactNode;
  /** 0..1 — renders a usage bar (e.g. memory vs. limit). */
  ratio?: number | null;
  children?: ReactNode;
}

export function MetricCard({ label, value, hint, icon, ratio, children }: MetricCardProps) {
  const pct = ratio === null || ratio === undefined ? null : Math.min(1, Math.max(0, ratio));
  const barColor = pct === null ? "" : pct > 0.9 ? "bg-bad" : pct > 0.7 ? "bg-warn" : "bg-accent";
  return (
    <div className="card flex min-w-0 flex-col gap-2 p-4">
      <div className="flex items-center justify-between gap-2 text-xs font-medium tracking-wide text-muted uppercase">
        {label}
        {icon}
      </div>
      <div
        className="truncate text-2xl font-semibold tabular-nums"
        data-testid={`metric-${label.toLowerCase()}`}
      >
        {value}
      </div>
      {pct !== null && (
        <div className="h-1.5 overflow-hidden rounded-full bg-raised" aria-hidden>
          <div
            className={`h-full rounded-full ${barColor} transition-[width]`}
            style={{ width: `${pct * 100}%` }}
          />
        </div>
      )}
      {hint && <div className="truncate text-xs text-subtle">{hint}</div>}
      {children}
    </div>
  );
}

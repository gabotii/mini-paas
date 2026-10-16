import type { StatusMeta, Tone } from "../lib/status";

const TONES: Record<Tone, { dot: string; badge: string }> = {
  green: { dot: "bg-ok", badge: "bg-ok/10 text-ok border-ok/25" },
  yellow: { dot: "bg-warn", badge: "bg-warn/10 text-warn border-warn/25" },
  blue: { dot: "bg-info", badge: "bg-info/10 text-info border-info/25" },
  red: { dot: "bg-bad", badge: "bg-bad/10 text-bad border-bad/25" },
  gray: { dot: "bg-idle", badge: "bg-idle/10 text-muted border-idle/25" },
  violet: { dot: "bg-accent", badge: "bg-accent/10 text-accent border-accent/25" },
};

interface StatusBadgeProps {
  meta: StatusMeta;
  size?: "sm" | "md";
}

export function StatusDot({ meta }: { meta: StatusMeta }) {
  return (
    <span
      aria-hidden
      className={`inline-block h-2 w-2 shrink-0 rounded-full ${TONES[meta.tone].dot} ${meta.pulse ? "animate-pulse-dot" : ""}`}
    />
  );
}

export function StatusBadge({ meta, size = "md" }: StatusBadgeProps) {
  const padding = size === "sm" ? "px-2 py-0.5 text-[11px]" : "px-2.5 py-1 text-xs";
  return (
    <span
      data-testid="status-badge"
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium ${padding} ${TONES[meta.tone].badge}`}
    >
      <StatusDot meta={meta} />
      {meta.label}
    </span>
  );
}

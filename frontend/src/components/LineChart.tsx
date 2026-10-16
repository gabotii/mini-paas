import { useId, useMemo } from "react";

interface Point {
  t: number;
  v: number;
}

interface LineChartProps {
  points: Point[];
  /** Fixed upper bound (e.g. memory limit); defaults to the data maximum. */
  max?: number;
  format: (value: number) => string;
  label: string;
  color?: string;
  height?: number;
}

const WIDTH = 600;

/** Dependency-free responsive area chart (SVG with a viewBox, scales to its container). */
export function LineChart({
  points,
  max,
  format,
  label,
  color = "var(--color-accent)",
  height = 160,
}: LineChartProps) {
  const gradientId = useId();
  const { path, area, top, latest } = useMemo(() => {
    if (points.length < 2) return { path: "", area: "", top: max ?? 0, latest: points[0]?.v ?? null };
    const t0 = points[0]!.t;
    const span = Math.max(1, points[points.length - 1]!.t - t0);
    const peak = Math.max(max ?? 0, ...points.map((p) => p.v), 1e-9);
    const pad = 6;
    const xy = points.map((p) => [
      ((p.t - t0) / span) * WIDTH,
      height - pad - (p.v / peak) * (height - pad * 2),
    ]);
    const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x!.toFixed(1)},${y!.toFixed(1)}`).join(" ");
    return {
      path: line,
      area: `${line} L${WIDTH},${height} L0,${height} Z`,
      top: peak,
      latest: points[points.length - 1]!.v,
    };
  }, [points, max, height]);

  return (
    <figure className="card p-4">
      <figcaption className="mb-3 flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-xs text-muted tabular-nums">
          {latest === null ? "no data" : `now ${format(latest)} · peak ${format(top)}`}
        </span>
      </figcaption>
      {points.length < 2 ? (
        <div className="flex items-center justify-center text-xs text-subtle" style={{ height }}>
          Collecting samples…
        </div>
      ) : (
        <svg
          viewBox={`0 0 ${WIDTH} ${height}`}
          preserveAspectRatio="none"
          className="w-full"
          style={{ height }}
          role="img"
          aria-label={label}
        >
          <defs>
            <linearGradient id={gradientId} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity="0.35" />
              <stop offset="100%" stopColor={color} stopOpacity="0" />
            </linearGradient>
          </defs>
          {[0.25, 0.5, 0.75].map((f) => (
            <line
              key={f}
              x1="0"
              x2={WIDTH}
              y1={height * f}
              y2={height * f}
              stroke="var(--color-line)"
              strokeDasharray="4 6"
            />
          ))}
          <path d={area} fill={`url(#${gradientId})`} />
          <path d={path} fill="none" stroke={color} strokeWidth="2" vectorEffect="non-scaling-stroke" />
        </svg>
      )}
    </figure>
  );
}

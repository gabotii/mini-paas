import { Clock, Cpu, HardDrive, Network, RotateCw } from "lucide-react";
import { useEffect, useMemo } from "react";

import { LineChart } from "../../components/LineChart";
import { LoadingState } from "../../components/LoadingState";
import { MetricCard } from "../../components/MetricCard";
import { formatBytes, formatDuration, formatPercent } from "../../lib/format";
import { useMetrics } from "../../stores/metrics";
import { toast } from "../../stores/toast";
import { useProject } from "./context";

const WINDOWS = [
  { minutes: 15, label: "15m" },
  { minutes: 60, label: "1h" },
  { minutes: 360, label: "6h" },
  { minutes: 1440, label: "24h" },
];

export function MetricsTab() {
  const project = useProject();
  const data = useMetrics((s) => s.byProject[project.id]);
  const fetch = useMetrics((s) => s.fetch);
  const window = data?.window ?? 60;

  // Initial load per window; afterwards live samples arrive over the event stream.
  useEffect(() => {
    fetch(project.id, window).catch((error: unknown) =>
      toast.error("Could not load metrics", (error as Error).message),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps -- refetch only when the project changes
  }, [project.id, fetch]);

  const series = useMemo(() => {
    const samples = data?.samples ?? [];
    const point = (pick: (s: (typeof samples)[number]) => number) =>
      samples.map((s) => ({ t: new Date(s.timestamp).getTime(), v: pick(s) }));
    // Network counters are cumulative: chart the per-interval throughput.
    const rate = samples.slice(1).map((s, i) => {
      const prev = samples[i]!;
      const seconds = Math.max(
        1,
        (new Date(s.timestamp).getTime() - new Date(prev.timestamp).getTime()) / 1000,
      );
      const delta = s.network_rx + s.network_tx - prev.network_rx - prev.network_tx;
      return { t: new Date(s.timestamp).getTime(), v: Math.max(0, delta) / seconds };
    });
    return { cpu: point((s) => s.cpu_usage), memory: point((s) => s.memory_usage), network: rate };
  }, [data?.samples]);

  if (!data) return <LoadingState label="Loading metrics…" />;
  const current = data.current;
  const memoryLimit = current?.memory_limit ?? project.memory_limit_mb * 1024 * 1024;

  return (
    <section className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted">
          {project.status === "RUNNING"
            ? "Sampled from docker stats every few seconds."
            : "The application is not running."}
        </p>
        <div className="flex rounded-lg border border-line p-0.5" role="group" aria-label="Time window">
          {WINDOWS.map((w) => (
            <button
              key={w.minutes}
              onClick={() => void fetch(project.id, w.minutes)}
              aria-pressed={window === w.minutes}
              className={`rounded-md px-3 py-1 text-xs ${window === w.minutes ? "bg-raised text-fg" : "text-muted hover:text-fg"}`}
            >
              {w.label}
            </button>
          ))}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <MetricCard
          label="CPU"
          value={formatPercent(current?.cpu_usage)}
          icon={<Cpu className="h-4 w-4" />}
        />
        <MetricCard
          label="Memory"
          value={formatBytes(current?.memory_usage)}
          icon={<HardDrive className="h-4 w-4" />}
          ratio={current ? current.memory_usage / current.memory_limit : null}
          hint={`of ${formatBytes(memoryLimit, 0)}`}
        />
        <MetricCard
          label="Network RX"
          value={formatBytes(current?.network_rx)}
          icon={<Network className="h-4 w-4" />}
        />
        <MetricCard
          label="Network TX"
          value={formatBytes(current?.network_tx)}
          icon={<Network className="h-4 w-4" />}
        />
        <MetricCard
          label="Uptime"
          value={formatDuration(current?.uptime_seconds)}
          icon={<Clock className="h-4 w-4" />}
          hint={current ? `${current.restart_count} restarts` : undefined}
        >
          {current && current.restart_count > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-warn">
              <RotateCw className="h-3 w-3" aria-hidden /> restarted
            </span>
          )}
        </MetricCard>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <LineChart label="CPU usage" points={series.cpu} format={formatPercent} />
        <LineChart
          label="Memory"
          points={series.memory}
          max={memoryLimit}
          format={(v) => formatBytes(v)}
          color="var(--color-info)"
        />
        <div className="lg:col-span-2">
          <LineChart
            label="Network throughput"
            points={series.network}
            format={(v) => `${formatBytes(v)}/s`}
            color="var(--color-ok)"
          />
        </div>
      </div>
    </section>
  );
}

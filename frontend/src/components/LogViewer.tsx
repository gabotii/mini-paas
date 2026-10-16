import { ArrowDown, Download, Eraser, Pause, Play, Search } from "lucide-react";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { formatClock } from "../lib/format";
import type { SocketState } from "../services/socket";
import type { LogLevel, LogLine, LogSource } from "../types/api";

const LEVEL_CLASS: Record<LogLevel, string> = {
  DEBUG: "text-subtle",
  INFO: "text-fg/90",
  WARNING: "text-warn",
  ERROR: "text-bad",
};

const SOURCE_CLASS: Record<LogSource, string> = {
  build: "text-warn/80",
  deploy: "text-info",
  runtime: "text-ok/80",
  system: "text-accent",
};

const SOURCES: (LogSource | "all")[] = ["all", "build", "deploy", "runtime", "system"];

interface LogViewerProps {
  lines: LogLine[];
  connection?: SocketState;
  onClear?: () => void;
  emptyText?: string;
  height?: string;
  downloadName?: string;
}

export function LogViewer({
  lines,
  connection,
  onClear,
  emptyText = "Waiting for logs…",
  height = "h-[55vh] min-h-72",
  downloadName = "deployment.log",
}: LogViewerProps) {
  const scroller = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const [timestamps, setTimestamps] = useState(true);
  const [source, setSource] = useState<LogSource | "all">("all");
  const [hideDebug, setHideDebug] = useState(true);
  const [query, setQuery] = useState("");

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return lines.filter(
      (line) =>
        (source === "all" || line.source === source) &&
        (!hideDebug || line.level !== "DEBUG") &&
        (!needle || line.message.toLowerCase().includes(needle)),
    );
  }, [lines, source, hideDebug, query]);

  // Auto-scroll to the newest line while following.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (follow && el) el.scrollTop = el.scrollHeight;
  }, [visible, follow]);

  // Scrolling up pauses following; scrolling back to the bottom resumes it.
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const onScroll = () => {
      const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      setFollow(atBottom);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  const download = () => {
    const text = lines
      .map((l) => `${l.timestamp} ${l.level.padEnd(7)} [${l.source}] ${l.message}`)
      .join("\n");
    const url = URL.createObjectURL(new Blob([text], { type: "text/plain" }));
    const anchor = Object.assign(document.createElement("a"), { href: url, download: downloadName });
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="flex flex-col overflow-hidden rounded-xl border border-line bg-terminal">
      <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 py-2">
        {connection && <ConnectionPill state={connection} />}
        <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by source">
          {SOURCES.map((s) => (
            <button
              key={s}
              onClick={() => setSource(s)}
              aria-pressed={source === s}
              className={`rounded-md px-2 py-1 text-xs capitalize transition-colors ${
                source === s ? "bg-raised text-fg" : "text-muted hover:text-fg"
              }`}
            >
              {s}
            </button>
          ))}
        </div>
        <label className="relative order-last w-full sm:order-none sm:ml-auto sm:w-48">
          <Search className="pointer-events-none absolute top-1/2 left-2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search logs"
            aria-label="Search logs"
            className="input h-8 py-1 pl-7 text-xs"
          />
        </label>
        <div className="ml-auto flex items-center gap-1 sm:ml-0">
          <ToolbarButton
            label={hideDebug ? "Show debug lines" : "Hide debug lines"}
            onClick={() => setHideDebug((v) => !v)}
          >
            <span className={`text-[10px] font-semibold ${hideDebug ? "text-subtle" : "text-fg"}`}>DBG</span>
          </ToolbarButton>
          <ToolbarButton
            label={timestamps ? "Hide timestamps" : "Show timestamps"}
            onClick={() => setTimestamps((v) => !v)}
          >
            <span className={`font-mono text-[10px] ${timestamps ? "text-fg" : "text-subtle"}`}>12:00</span>
          </ToolbarButton>
          <ToolbarButton
            label={follow ? "Pause auto-scroll" : "Resume auto-scroll"}
            onClick={() => setFollow((v) => !v)}
          >
            {follow ? <Pause className="h-3.5 w-3.5" /> : <Play className="h-3.5 w-3.5" />}
          </ToolbarButton>
          <ToolbarButton label="Download logs" onClick={download}>
            <Download className="h-3.5 w-3.5" />
          </ToolbarButton>
          {onClear && (
            <ToolbarButton label="Clear view" onClick={onClear}>
              <Eraser className="h-3.5 w-3.5" />
            </ToolbarButton>
          )}
        </div>
      </div>

      <div className="relative">
        <div
          ref={scroller}
          data-testid="log-viewer"
          role="log"
          aria-live="off"
          className={`${height} overflow-auto px-3 py-3 font-mono text-[12.5px] leading-relaxed`}
        >
          {visible.length === 0 ? (
            <p className="text-subtle">{lines.length ? "No lines match the current filters." : emptyText}</p>
          ) : (
            visible.map((line) => (
              <div key={line.id} className="flex gap-3 whitespace-pre-wrap break-all hover:bg-white/[0.03]">
                {timestamps && (
                  <span className="shrink-0 text-subtle select-none">[{formatClock(line.timestamp)}]</span>
                )}
                <span className={`hidden w-14 shrink-0 select-none sm:inline ${SOURCE_CLASS[line.source]}`}>
                  {line.source}
                </span>
                <span
                  className={
                    line.message.startsWith("$ ") ? "font-semibold text-accent" : LEVEL_CLASS[line.level]
                  }
                >
                  {line.message}
                </span>
              </div>
            ))
          )}
        </div>
        {!follow && visible.length > 0 && (
          <button
            onClick={() => setFollow(true)}
            className="absolute right-4 bottom-4 inline-flex items-center gap-1.5 rounded-full border border-line bg-raised px-3 py-1.5 text-xs shadow-lg hover:bg-line"
          >
            <ArrowDown className="h-3.5 w-3.5" /> Jump to latest
          </button>
        )}
      </div>
    </div>
  );
}

function ToolbarButton({
  label,
  onClick,
  children,
}: {
  label: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="inline-flex h-8 w-8 items-center justify-center rounded-md text-muted hover:bg-raised hover:text-fg"
    >
      {children}
    </button>
  );
}

function ConnectionPill({ state }: { state: SocketState }) {
  const map = {
    open: { label: "Live", cls: "bg-ok" },
    connecting: { label: "Connecting", cls: "bg-warn animate-pulse-dot" },
    closed: { label: "Reconnecting", cls: "bg-bad animate-pulse-dot" },
  } as const;
  const { label, cls } = map[state];
  return (
    <span className="inline-flex items-center gap-1.5 rounded-md bg-raised px-2 py-1 text-xs text-muted">
      <span className={`h-1.5 w-1.5 rounded-full ${cls}`} aria-hidden />
      {label}
    </span>
  );
}

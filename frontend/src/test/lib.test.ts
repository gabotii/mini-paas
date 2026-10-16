import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatPercent, relativeTime, shortHash } from "../lib/format";
import { effectiveProjectStatus } from "../lib/status";
import { passwordProblems, validateArchive } from "../lib/validation";
import { MAX_LINES, mergeLogLines } from "../stores/logs";
import type { LogLine, Project } from "../types/api";

describe("format", () => {
  it("formats bytes", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(248 * 1024 * 1024)).toBe("248 MB");
    expect(formatBytes(null)).toBe("—");
  });

  it("formats durations", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(151)).toBe("2m 31s");
    expect(formatDuration(14 * 60 + 32)).toBe("14m 32s");
    expect(formatDuration(3 * 3600 + 120)).toBe("3h 2m");
    expect(formatDuration(null)).toBe("—");
  });

  it("formats percentages and hashes", () => {
    expect(formatPercent(3.456)).toBe("3.5%");
    expect(formatPercent(12.4)).toBe("12%");
    expect(shortHash("abcdef1234567")).toBe("abcdef1");
  });

  it("formats relative times", () => {
    const now = Date.parse("2026-10-05T12:00:00Z");
    expect(relativeTime("2026-10-05T11:59:50Z", now)).toBe("just now");
    expect(relativeTime("2026-10-05T11:55:00Z", now)).toBe("5 minutes ago");
    expect(relativeTime("2026-10-05T09:00:00Z", now)).toBe("3 hours ago");
  });
});

describe("validation", () => {
  it("validates archives", () => {
    const zip = new File(["PK"], "app.zip", { type: "application/zip" });
    expect(validateArchive(zip, 1)).toBeNull();
    expect(validateArchive(new File(["x"], "app.tar.gz"), 1)).toMatch(/zip/);
    expect(validateArchive(new File([], "empty.zip"), 1)).toMatch(/empty/);
    const big = new File([new Uint8Array(2 * 1024 * 1024)], "big.zip");
    expect(validateArchive(big, 1)).toMatch(/1 MB/);
  });

  it("mirrors the backend password policy", () => {
    expect(passwordProblems("short")).toMatch(/10/);
    expect(passwordProblems("alllowercaseletters")).toMatch(/three/);
    expect(passwordProblems("Good-Password1")).toBeNull();
  });
});

function line(id: number): LogLine {
  return {
    id,
    deployment_id: "d",
    timestamp: "2026-01-01T00:00:00Z",
    level: "INFO",
    source: "build",
    message: `l${id}`,
  };
}

describe("mergeLogLines", () => {
  it("appends only newer lines (backlog/live overlap is de-duplicated)", () => {
    const merged = mergeLogLines([line(1), line(2)], [line(2), line(3)]);
    expect(merged.map((l) => l.id)).toEqual([1, 2, 3]);
  });

  it("keeps the newest MAX_LINES lines", () => {
    const existing = Array.from({ length: MAX_LINES }, (_, i) => line(i + 1));
    const merged = mergeLogLines(existing, [line(MAX_LINES + 1)]);
    expect(merged).toHaveLength(MAX_LINES);
    expect(merged[0]!.id).toBe(2);
  });
});

describe("status", () => {
  it("prefers an in-progress deployment over the project state", () => {
    const base = { status: "RUNNING", active_deployment: null } as unknown as Project;
    expect(effectiveProjectStatus(base).label).toBe("Running");
    const building = { ...base, active_deployment: { status: "BUILDING" } } as unknown as Project;
    expect(effectiveProjectStatus(building)).toMatchObject({
      label: "Building",
      tone: "yellow",
      pulse: true,
    });
  });
});

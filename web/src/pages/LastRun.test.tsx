import { describe, expect, it } from "vitest";
import { screen, within } from "@testing-library/react";
import { renderApp } from "@/test/utils";
import { LastRunCard } from "./Connections";
import type { LastRun } from "@/api/types";

const run = (o: Partial<LastRun> = {}): LastRun => ({
  run: "20261005-073000-ab12", started: "2026-10-05T07:30:00", ended: "2026-10-05T07:30:05", outcome: "ok", duration_s: 5.2, failed: [], warned: [],
  steps: [
    { step: "sync", status: "ok", ms: 1234, counts: { new: 4 } },
    { step: "memory", status: "warn", ms: 90, counts: { errors: 1 } },
    { step: "classify", status: "skipped", ms: 0, counts: {} },
  ],
  ...o,
});

describe("last scheduled run card", () => {
  it("shows the run id, the outcome and every step with its duration", () => {
    renderApp(<LastRunCard r={run({ warned: ["memory"] })} />);
    expect(screen.getByText(/run 20261005-073000-ab12/)).toBeInTheDocument();
    const steps = within(screen.getByRole("list", { name: "Steps of the last run" }));
    expect(steps.getByText("sync").closest("li")).toHaveTextContent("1234 ms");
    expect(steps.getByText("classify").closest("li")).toHaveTextContent("skipped");
    expect(screen.getByText("ok")).toBeInTheDocument();
  });

  it("names the failed steps and where to look, never an error text", () => {
    renderApp(<LastRunCard r={run({ outcome: "failed", failed: ["sync"], steps: [{ step: "sync", status: "error", ms: 12, counts: {}, error: "ApiError" }] })} />);
    expect(screen.getByText(/Failed: sync/)).toBeInTheDocument();
    expect(screen.getByText(/coach logs --run 20261005-073000-ab12/)).toBeInTheDocument();
  });

  it("says so when no run was ever logged", () => {
    renderApp(<LastRunCard r={null} />);
    expect(screen.getByText(/No scheduled run has been logged yet/)).toBeInTheDocument();
  });
});

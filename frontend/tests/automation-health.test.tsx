/**
 * The verdict an operator reads before trusting any number on the dashboard.
 *
 * The audit's objection was that the UI reported the scheduler's *configuration*.
 * A scheduler thread can die inside the API process, leaving /health green and nothing
 * chased, while configuration still says "enabled". These pin the two ways this
 * component must not repeat that mistake: it must not call a stopped agent healthy,
 * and it must not call a fresh deployment broken.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { AutomationHealth } from "@/components/AutomationHealth";
import type { AutomationHealth as Health, AutomationJob } from "@/lib/api";

function job(overrides: Partial<AutomationJob> = {}): AutomationJob {
  return {
    job_id: "recovery_cycle",
    label: "Daily recovery cycle",
    state: "healthy",
    explanation: "Running on schedule.",
    last_run_at: new Date().toISOString(),
    last_run_status: "succeeded",
    last_success_at: new Date().toISOString(),
    last_error: null,
    last_duration_ms: 1200,
    last_detail: { sent: 3, held: 1 },
    next_run_at: new Date(Date.now() + 3_600_000).toISOString(),
    ...overrides,
  };
}

function health(overrides: Partial<Health> = {}): Health {
  return {
    overall: "healthy",
    scheduler_enabled: true,
    scheduler_running_here: true,
    checked_at: new Date().toISOString(),
    jobs: [job()],
    ...overrides,
  };
}

describe("AutomationHealth", () => {
  it("says plainly when the agent is running", () => {
    const html = renderToStaticMarkup(<AutomationHealth health={health()} />);
    expect(html).toContain("Automation is running on schedule.");
    expect(html).toContain("Daily recovery cycle");
  });

  it("shows what the last run actually did, not just that it happened", () => {
    const html = renderToStaticMarkup(<AutomationHealth health={health()} />);
    expect(html).toContain("3 sent");
  });

  it("does not call a stopped agent healthy", () => {
    const html = renderToStaticMarkup(
      <AutomationHealth
        health={health({
          overall: "stale",
          jobs: [job({ state: "stale", explanation: "Last successful run was about 96 hours ago." })],
        })}
      />,
    );
    expect(html).toContain("Automation has not run recently.");
    expect(html).toContain("96 hours ago");
  });

  it("names a failure rather than softening it", () => {
    const html = renderToStaticMarkup(
      <AutomationHealth
        health={health({
          overall: "failing",
          jobs: [job({ state: "failing", explanation: "The most recent run failed: razorpay unreachable." })],
        })}
      />,
    );
    expect(html).toContain("Automation is failing.");
    expect(html).toContain("razorpay unreachable");
  });

  it("treats a deployment with no history as unknown, not broken", () => {
    // A false alarm on every first boot trains an operator to ignore the banner,
    // which is worse than not having one.
    const html = renderToStaticMarkup(
      <AutomationHealth
        health={health({
          overall: "unknown",
          jobs: [job({ state: "unknown", last_success_at: null, last_run_at: null })],
        })}
      />,
    );
    expect(html).toContain("No automation history on this deployment yet.");
    expect(html).not.toContain("Automation is failing.");
  });

  it("explains a scheduler that is enabled but not running in this process", () => {
    const html = renderToStaticMarkup(
      <AutomationHealth health={health({ scheduler_running_here: false })} />,
    );
    expect(html).toContain("only one of them runs the schedule");
  });
});

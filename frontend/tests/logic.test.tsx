import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { blockReason } from "@/components/alerts/ActionRow";
import { RiskRunway, levelOf } from "@/components/shared/RiskRunway";
import { can } from "@/store/session";
import { dayLabel, eta, pct, timeAgo } from "@/utils/format";
import type { User } from "@/types";

describe("risk levels", () => {
  it("uses the 60 / 80 defaults and honours per-service overrides", () => {
    expect(levelOf(59.9)).toBe("ok");
    expect(levelOf(60)).toBe("warning");
    expect(levelOf(80)).toBe("critical");
    expect(levelOf(70, 75, 90)).toBe("ok");
    expect(levelOf(null)).toBe("ok");
  });
  it("describes the runway accessibly", () => {
    render(<RiskRunway score={72} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/Risk 72 of 100, warning/);
  });
});

describe("approval guardrails (mirror of the server rules)", () => {
  it("withholds every approval from junior engineers", () => {
    expect(blockReason("junior_engineer", false, { risk_level: "low" })).toMatch(/withheld/);
  });
  it("routes high-risk actions away from guided-mode accounts only", () => {
    expect(blockReason("developer", true, { risk_level: "high" })).toMatch(/high-risk/);
    expect(blockReason("developer", true, { risk_level: "low" })).toBeNull();
    expect(blockReason("developer", false, { risk_level: "high" })).toBeNull();
    expect(blockReason("sre", false, { risk_level: "high" })).toBeNull();
  });
  it("viewers can never decide", () => {
    expect(blockReason("viewer", false, { risk_level: "low" })).toMatch(/Viewers/);
  });
});

describe("permissions", () => {
  const u = { permissions: ["chat.use"] } as unknown as User;
  it("checks the permission list", () => {
    expect(can(u, "chat.use")).toBe(true);
    expect(can(u, "policy.edit")).toBe(false);
    expect(can(null, "chat.use")).toBe(false);
  });
});

describe("formatting", () => {
  it("formats ETAs the way the agent speaks", () => {
    expect(eta(10, 15)).toBe("about 10 to 15 min");
    expect(eta(0, 3)).toBe("impact imminent");
    expect(eta(null, null)).toBeNull();
  });
  it("formats percentages and relative times", () => {
    expect(pct(0.7134)).toBe("71%");
    expect(pct(null)).toBe("n/a");
    expect(timeAgo(new Date(Date.now() - 5 * 60000).toISOString())).toMatch(/5 minutes ago/);
    expect(dayLabel(new Date().toISOString())).toBe("Today");
  });
});

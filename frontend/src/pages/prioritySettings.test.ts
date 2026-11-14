import { describe, expect, it } from "vitest";
import { balance } from "./PrioritySettingsPage";

const sum = (p: Record<string, number>) => Object.values(p).reduce((s, v) => s + v, 0);

describe("balance()", () => {
  it("scales proportionally to exactly 100", () => {
    const out = balance({ impact: 50, severity: 50, recurrence: 20, growth: 0, business: 0 });
    expect(sum(out)).toBe(100);
    expect(out.impact).toBe(out.severity);
    expect(out.growth).toBe(0);
  });
  it("hands rounding leftovers out so the total is never 99 or 101", () => {
    expect(sum(balance({ impact: 1, severity: 1, recurrence: 1, growth: 0, business: 0 }))).toBe(100);
  });
  it("splits evenly when everything is zero", () => {
    expect(balance({ impact: 0, severity: 0, recurrence: 0, growth: 0, business: 0 }))
      .toEqual({ impact: 20, severity: 20, recurrence: 20, growth: 20, business: 20 });
  });
});

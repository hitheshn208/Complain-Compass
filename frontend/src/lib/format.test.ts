import { describe, expect, it } from "vitest";
import { fmtInt, fmtScore, humanize, seriesColor, sourceColor, truncate } from "./format";
import { activeFilterCount, parseFilters } from "../hooks/useFilters";

describe("format helpers", () => {
  it("formats numbers and missing values", () => {
    expect(fmtInt(1234)).toBe("1,234");
    expect(fmtInt(null)).toBe("–");
    expect(fmtScore(72.456)).toBe("72.5");
  });
  it("humanizes enum values", () => {
    expect(humanize("SOCIAL_MEDIA")).toBe("Social Media");
    expect(humanize("age_group")).toBe("Age Group");
  });
  it("truncates long text", () => {
    expect(truncate("abcdef", 4)).toBe("abc…");
    expect(truncate("abc", 4)).toBe("abc");
  });
  it("keeps categorical colors stable and folds overflow to muted", () => {
    expect(sourceColor("EMAIL")).toBe("var(--s1)");
    expect(sourceColor("CHAT")).toBe("var(--s2)");
    expect(seriesColor(8)).toBe("var(--muted)");
  });
});

describe("URL filters", () => {
  it("parses list and date params", () => {
    const f = parseFilters(new URLSearchParams("country=India,Canada&date_from=2026-10-01&severity=HIGH"));
    expect(f.country).toEqual(["India", "Canada"]);
    expect(f.severity).toEqual(["HIGH"]);
    expect(activeFilterCount(f)).toBe(3);
  });
});

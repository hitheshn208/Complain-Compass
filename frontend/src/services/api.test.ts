import { describe, expect, it } from "vitest";
import { buildQuery, errorMessage } from "./api";

describe("buildQuery", () => {
  it("returns empty string with no params", () => {
    expect(buildQuery()).toBe("");
    expect(buildQuery({ country: [] })).toBe("");
  });
  it("joins list filters with commas and includes extras", () => {
    const qs = buildQuery({ country: ["India", "Canada"], date_from: "2026-10-01" }, { page: 2, search: undefined });
    const p = new URLSearchParams(qs.slice(1));
    expect(p.get("country")).toBe("India,Canada");
    expect(p.get("date_from")).toBe("2026-10-01");
    expect(p.get("page")).toBe("2");
    expect(p.has("search")).toBe(false);
  });
});

describe("errorMessage", () => {
  it("reads the backend error envelope", () => {
    expect(errorMessage({ error: { code: 409, message: "dup" } }, "x")).toBe("dup");
    expect(errorMessage(null, "fallback")).toBe("fallback");
  });
});

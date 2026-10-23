import { describe, expect, it } from "vitest";

import { isUnseen, later } from "@/lib/seenOnDevice";

describe("whether a time is unseen", () => {
  const before = "2026-10-20T08:00:00+00:00";
  const after = "2026-10-20T09:00:00+00:00";

  it("没存过时有这一刻就算没看过，看过以后不算，更晚的又算", () => {
    expect(isUnseen(before, null)).toBe(true);
    expect(isUnseen(before, before)).toBe(false);
    expect(isUnseen(after, before)).toBe(true);
    expect(isUnseen(null, null)).toBe(false);
  });

  it("比的是时刻，不是写法", () => {
    expect(isUnseen("2026-10-20T16:00:00+08:00", before)).toBe(false);
  });
});

describe("the latest of two times", () => {
  it("is the later one, either way round, nothing being earlier than any", () => {
    expect(later("2026-10-20T08:00:00+00:00", "2026-10-20T09:00:00+00:00")).toBe("2026-10-20T09:00:00+00:00");
    expect(later("2026-10-20T09:00:00+00:00", "2026-10-20T08:00:00+00:00")).toBe("2026-10-20T09:00:00+00:00");
    expect(later(null, "2026-10-20T08:00:00+00:00")).toBe("2026-10-20T08:00:00+00:00");
    expect(later("2026-10-20T08:00:00+00:00", null)).toBe("2026-10-20T08:00:00+00:00");
    expect(later(null, null)).toBeNull();
  });
});

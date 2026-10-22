import { describe, expect, it } from "vitest";

import { scheduleText } from "@/lib/schedule";

describe("她现在大概在干嘛", () => {
  it("在某一段安排里写那一段，两段之间写有空，没有行程什么都不写", () => {
    expect(scheduleText({ what: "舞蹈课", where: "事务所", kind: "lesson", until: "12:00" })).toBe("舞蹈课 · 事务所");
    expect(scheduleText({ what: "在家休息", where: "", kind: "rest", until: "22:00" })).toBe("在家休息");
    expect(scheduleText({ free: true })).toBe("现在有空");
    expect(scheduleText(null)).toBeNull();
  });
});

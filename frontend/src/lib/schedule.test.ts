import { describe, expect, it } from "vitest";

import { scheduleText } from "@/lib/schedule";

describe("她现在大概在干嘛", () => {
  it("在某一段安排里写那一段，两段之间写有空，没有行程什么都不写", () => {
    expect(scheduleText({ what: "舞蹈课", where: "事务所", kind: "lesson", until: "12:00" })).toBe("舞蹈课 · 事务所");
    expect(scheduleText({ what: "在家休息", where: "", kind: "rest", until: "22:00" })).toBe("在家休息");
    expect(scheduleText({ free: true })).toBe("现在有空");
    expect(scheduleText(null)).toBeNull();
  });

  it("睡着了写在睡觉，被你吵醒了写出来，快到睡觉的时间写准备睡了", () => {
    expect(scheduleText({ asleep: true, woken: false })).toBe("在睡觉 💤");
    expect(scheduleText({ asleep: true, woken: true })).toBe("被你吵醒了");
    expect(scheduleText({ free: true, bedtime: true })).toBe("准备睡了");
    expect(scheduleText({ what: "看动画", where: "家里", kind: "private", until: "23:30", bedtime: true })).toBe(
      "准备睡了",
    );
  });

  it("在洗澡：成年的偶像写在洗澡，别的只写暂时不在手机旁", () => {
    expect(scheduleText({ bath: true })).toBe("在洗澡 🛁");
    expect(scheduleText({ away: true })).toBe("暂时不在手机旁");
  });
});

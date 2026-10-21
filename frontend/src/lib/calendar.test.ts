import { describe, expect, it } from "vitest";

import { monthEvents, monthWeeks, shiftMonth, todayIn, type CalendarYear } from "@/lib/calendar";

const SAKURA = { name: "樱花季", note: "", icon: "🌸", start: { month: 3, day: 25 }, end: { month: 4, day: 10 } };
const VALENTINE = { name: "情人节", note: "", icon: "", start: { month: 2, day: 14 }, end: null };

const YEAR: CalendarYear = {
  birthdays: [
    { character_id: "mano", month: 4, day: 25 },
    { character_id: "chiyoko", month: 2, day: 24 },
    { character_id: "leap", month: 2, day: 29 },
  ],
  holidays: [VALENTINE, SAKURA],
};

describe("月历的格子", () => {
  it("星期一在最左边，月初前面空出来，最后一行补齐", () => {
    // 2026 年 10 月 1 日是星期四，31 天。
    const weeks = monthWeeks(2026, 10);
    expect(weeks[0]).toEqual([null, null, null, 1, 2, 3, 4]);
    expect(weeks.at(-1)).toEqual([26, 27, 28, 29, 30, 31, null]);
    expect(weeks.every((w) => w.length === 7)).toBe(true);
  });
});

describe("翻月", () => {
  it("跨年时年份跟着变", () => {
    expect(shiftMonth(2026, 12, 1)).toEqual({ year: 2027, month: 1 });
    expect(shiftMonth(2026, 1, -1)).toEqual({ year: 2025, month: 12 });
  });
});

describe("这个月有事的日子", () => {
  it("生日、制作人的生日、节日按日期排，同一天的偶像在前", () => {
    expect(monthEvents(2026, 2, YEAR, { month: 2, day: 14 })).toEqual([
      { day: 14, events: [{ kind: "producer" }, { kind: "holiday", holiday: VALENTINE }] },
      { day: 24, events: [{ kind: "birthday", characterId: "chiyoko" }] },
      { day: 28, events: [{ kind: "birthday", characterId: "leap" }] },
    ]);
  });

  it("2 月 29 日的生日在闰年就在 29 日", () => {
    expect(monthEvents(2028, 2, YEAR, null).map((d) => d.day)).toEqual([14, 24, 29]);
  });

  it("持续几天的节日只记在它在这个月的第一天", () => {
    expect(monthEvents(2026, 3, YEAR, null)).toEqual([{ day: 25, events: [{ kind: "holiday", holiday: SAKURA }] }]);
    expect(monthEvents(2026, 4, YEAR, null)).toEqual([
      { day: 1, events: [{ kind: "holiday", holiday: SAKURA }] },
      { day: 25, events: [{ kind: "birthday", characterId: "mano" }] },
    ]);
  });
});

describe("今天", () => {
  it("按制作人的时区算", () => {
    const now = new Date("2026-10-20T23:30:00Z");
    expect(todayIn("Europe/Madrid", now)).toEqual({ year: 2026, month: 10, day: 21 });
    expect(todayIn("America/New_York", now)).toEqual({ year: 2026, month: 10, day: 20 });
  });
});

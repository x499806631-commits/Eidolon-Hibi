import { describe, expect, it } from "vitest";

import { isUnread, monthDay, preview, type DiaryEntry } from "@/lib/diary";

function entry(change: Partial<DiaryEntry> = {}): DiaryEntry {
  return {
    id: 1,
    character_id: "mano",
    day: "2026-10-21",
    shown: "full",
    segments: [
      { ja: "今日は雨だった。", zh: "今天下雨了。" },
      { ja: "よかった。", zh: "太好了。" },
    ],
    more: false,
    secret: [],
    secret_hidden: false,
    read: false,
    ...change,
  };
}

describe("一篇日记", () => {
  it("清单上露出第一段的中文，合上的什么都不露", () => {
    expect(preview(entry())).toBe("今天下雨了。");
    expect(preview(entry({ shown: "closed", segments: [] }))).toBeNull();
  });

  it("日子按她写的那天", () => {
    expect(monthDay(entry())).toEqual({ month: 10, day: 21 });
  });

  it("合上的不算未读，读过的也不算", () => {
    expect(isUnread(entry())).toBe(true);
    expect(isUnread(entry({ shown: "closed", segments: [] }))).toBe(false);
    expect(isUnread(entry({ read: true }))).toBe(false);
  });
});

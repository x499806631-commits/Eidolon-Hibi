import { describe, expect, it } from "vitest";

import type { AffinityChange } from "@/lib/affinity";
import type { Entry } from "@/lib/conversationLog";
import { linesOf } from "@/lib/messageLines";

const UP: AffinityChange = { up: true, tier_changed: false, tier: "trust", cooldown: false, thawed: false };

function user(key: string, at = "2026-09-28T01:00:00Z"): Entry {
  return { kind: "user", key, text: "在吗", at };
}

function her(key: string, affinity: AffinityChange | null = null, at = "2026-09-28T01:00:01Z"): Entry {
  return { kind: "character", key, segments: [{ ja: "はい", zh: "好的" }], pending: false, at, affinity, photo: null };
}

function hungUp(key: string, at: string): Entry {
  return { kind: "event", key, event: "hung_up", at, gift: null, keepsakeId: null, affinity: null };
}

const NOW = new Date("2026-09-28T12:00:00Z");

describe("linesOf", () => {
  it("好感度不可用时，存下的变化不出胶囊", () => {
    const lines = linesOf([user("m1"), her("m2", UP)], NOW, false);

    expect(lines.some((line) => line.kind === "cue")).toBe(false);
  });

  it("变化的胶囊挂在改变了好感度的那条回复后面；换了一天先放日期条；同一个人连着发、中间没有日期条或胶囊的算一组", () => {
    const lines = linesOf(
      [
        user("m1", "2026-09-27T01:00:00Z"),
        user("m2", "2026-09-28T01:00:00Z"),
        user("m3", "2026-09-28T01:00:02Z"),
        her("m4", UP, "2026-09-28T01:00:03Z"),
        her("m5", null, "2026-09-28T01:00:04Z"),
      ],
      NOW,
      true,
    );

    const groups = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(groups).toEqual(["day", "m1<>", "day", "m2<", "m3>", "m4<>", "cue", "m5<>"]);
    expect(lines.find((line) => line.kind === "cue")).toEqual({ kind: "cue", key: "cue-m4", change: UP });
  });

  it("她的搭话总是另起一组：前一条她的话留着自己的时间，搭话重新带头像和名字", () => {
    const reachOut: Entry = {
      kind: "character",
      key: "r1",
      segments: [{ ja: "ねえ", zh: "呐" }],
      pending: false,
      at: "2026-09-28T09:00:00Z",
      affinity: null,
      photo: null,
      reachOut: true,
    };

    const lines = linesOf([user("m1"), her("m2"), reachOut], NOW, true);

    expect(lines.flatMap((line) => (line.kind === "entry" ? [[line.entry.key, line.first, line.last]] : []))).toEqual([
      ["m1", true, true],
      ["m2", true, true],
      ["r1", true, true],
    ]);
  });

  it("改变了好感度的送礼记录后面也挂胶囊", () => {
    const gift: Entry = { kind: "event", key: "e1", event: "gift", at: "2026-09-28T01:00:02Z", gift: "蛋糕", keepsakeId: null, affinity: UP };

    const lines = linesOf([user("m1"), gift, her("m2", null, "2026-09-28T01:00:03Z")], NOW, true);

    expect(lines.map((line) => (line.kind === "entry" ? line.entry.key : line.kind))).toEqual([
      "day",
      "m1",
      "e1",
      "cue",
      "m2",
    ]);
  });

  it("照片用完的胶囊挂在她推掉的那条回复后面（在变化胶囊之后），也把前后分成两组", () => {
    const usedUp = (key: string, affinity: AffinityChange | null = null): Entry => {
      const entry = her(key, affinity);
      return entry.kind === "character" ? { ...entry, photosUsedUp: "2026-09-28T02:00:00Z" } : entry;
    };
    const lines = linesOf([user("m1"), usedUp("m2"), her("m3"), usedUp("m4", UP), her("m5")], NOW, true);

    const groups = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(groups).toEqual(["day", "m1<>", "m2<>", "photos-used-up", "m3<", "m4>", "cue", "photos-used-up", "m5<>"]);
    expect(lines.find((line) => line.kind === "photos-used-up")).toEqual({
      kind: "photos-used-up",
      key: "photos-used-up-m2",
      retryAt: "2026-09-28T02:00:00Z",
    });
  });

  it("对话事件把前后分成两组，按它的时间参加跨天判断", () => {
    const lines = linesOf(
      [
        her("m1", null, "2026-09-27T01:00:00Z"),
        hungUp("e1", "2026-09-27T01:00:05Z"),
        her("m2", null, "2026-09-27T01:00:06Z"),
        hungUp("e2", "2026-09-28T01:00:00Z"),
        user("m3", "2026-09-28T01:00:01Z"),
      ],
      NOW,
      true,
    );

    const shown = lines.flatMap((line) =>
      line.kind === "entry" ? [`${line.entry.key}${line.first ? "<" : ""}${line.last ? ">" : ""}`] : [line.kind],
    );
    expect(shown).toEqual(["day", "m1<>", "e1<>", "m2<>", "day", "e2<>", "m3<>"]);
  });
});

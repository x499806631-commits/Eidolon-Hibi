import { describe, expect, it } from "vitest";

import { givenOn, giversIn, keepsakeImageUrl, ofIdol, type Keepsake } from "@/lib/keepsakes";

function keepsake(id: number, characterId: string, at = "2026-10-20T10:00:00Z"): Keepsake {
  return {
    id,
    character_id: characterId,
    occasion: "gift",
    about: "一块布丁",
    name: "手作的四叶草书签",
    look: "",
    emoji: "🍀",
    said: [{ ja: "これ", zh: "这个" }],
    has_image: false,
    letter_id: null,
    given_at: at,
    seen: false,
    drawn: 0,
  };
}

describe("keepsakes", () => {
  const all = [keepsake(3, "mano"), keepsake(2, "kogane"), keepsake(1, "mano")];

  it("filters by idol, all of them for null", () => {
    expect(ofIdol(all, null)).toEqual(all);
    expect(ofIdol(all, "mano").map((k) => k.id)).toEqual([3, 1]);
  });

  it("lists who gave something once each, in the idols' order", () => {
    expect(giversIn(all, ["kogane", "hiori", "mano"])).toEqual(["kogane", "mano"]);
  });

  it("knows its picture's address and the day it came", () => {
    expect(keepsakeImageUrl(all[0])).toBe("/api/keepsakes/3/image");
    const { year, month } = givenOn(keepsake(4, "mano", "2026-10-20T12:00:00Z"));
    expect([year, month]).toEqual([2026, 10]);
  });
});

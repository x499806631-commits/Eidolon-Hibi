import { describe, expect, it } from "vitest";

import { canBond, choicesFor, fallenOut, pickMaterials, willing, type Slot } from "@/lib/favors";
import type { GiftCharacter, GiftInfo } from "@/lib/gifts";

const gift = (id: string, category: GiftInfo["category"], rarity: GiftInfo["rarity"] = "common"): GiftInfo => ({
  id,
  name: id,
  category,
  rarity,
  retired: false,
});
const catalog = new Map([gift("cake", "food"), gift("pudding", "food"), gift("coffee", "drink"), gift("plush", "trinket", "rare")].map((g) => [g.id, g]));
const party: Slot[] = [
  { categories: ["food"], rarity: null },
  { categories: ["food"], rarity: null },
  { categories: ["drink"], rarity: null },
  { categories: ["flower", "trinket"], rarity: null },
];
const her = (tier: GiftCharacter["tier"], cooling = false): GiftCharacter => ({ id: "mano", tier, cooling, cooldown_until: null });

describe("拜托的材料", () => {
  it("先替你选好：每件选背包里第一件够格、还有剩的", () => {
    const backpack = [
      { gift_id: "cake", count: 2 },
      { gift_id: "coffee", count: 1 },
      { gift_id: "plush", count: 1 },
    ];
    expect(pickMaterials(party, backpack, catalog)).toEqual(["cake", "cake", "coffee", "plush"]);
  });

  it("不够的那件是 null", () => {
    expect(pickMaterials(party, [{ gift_id: "cake", count: 1 }], catalog)).toEqual(["cake", null, null, null]);
  });

  it("自己选过的还够格就留着", () => {
    const backpack = [
      { gift_id: "cake", count: 1 },
      { gift_id: "pudding", count: 1 },
    ];
    expect(pickMaterials(party.slice(0, 2), backpack, catalog, ["pudding", null])).toEqual(["pudding", "cake"]);
  });

  it("一件材料能选的，扣掉别的材料选走的", () => {
    const backpack = [
      { gift_id: "cake", count: 1 },
      { gift_id: "pudding", count: 1 },
    ];
    expect(choicesFor(0, party, ["cake", "pudding"], backpack, catalog).map((g) => g.id)).toEqual(["cake"]);
  });
});

describe("能不能拜托她", () => {
  it("冷淡、疏远、冷却中的不听拜托，可以托人和好", () => {
    expect(willing(her("distant"))).toBe(false);
    expect(willing(her("trust", true))).toBe(false);
    expect(willing(her("trust"))).toBe(true);
    expect(fallenOut(her("cold"))).toBe(true);
    expect(fallenOut(her("trust"))).toBe(false);
  });

  it("结拜要亲近以上", () => {
    expect(canBond(her("trust"))).toBe(false);
    expect(canBond(her("close"))).toBe(true);
  });
});

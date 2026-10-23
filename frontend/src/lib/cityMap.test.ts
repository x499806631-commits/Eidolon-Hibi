import { describe, expect, it } from "vitest";

import { copy } from "@/i18n";
import { byPlace, DATE_SPOTS, fanned, PLACE_AT, type IdolOnMap } from "@/lib/cityMap";
import { currentNavItem, isInShell, navDestination } from "@/lib/navShell";

describe("地图（GLOSSARY.md「地图」）", () => {
  it("在家的下一级：导航里「家」亮着，再点「家」回到屋里", () => {
    expect(isInShell("/home/map")).toBe(true);
    expect(currentNavItem("/home/map")).toBe("home");
    expect(navDestination("home", "/home/map")).toBe("/home");
  });

  it("每一处都在画里、有名字；能约的每一处都有先填好的一起做什么，不超过 30 个字", () => {
    for (const [place, [x, y]] of Object.entries(PLACE_AT)) {
      expect(x).toBeGreaterThan(0);
      expect(x).toBeLessThan(1);
      expect(y).toBeGreaterThan(0);
      expect(y).toBeLessThan(1);
      expect(copy.map.places[place as keyof typeof PLACE_AT]).toBeTruthy();
    }
    for (const spot of DATE_SPOTS) expect(copy.map.ideas[spot].length).toBeLessThanOrEqual(30);
  });

  it("同一处的人立在建筑上面排成一排，一个人就在正上方，谁也不和谁叠在一起", () => {
    expect(fanned(0, 1)[0]).toBe(0);
    expect(fanned(0, 1)[1]).toBeLessThan(0);
    const spots = Array.from({ length: 9 }, (_, i) => fanned(i, 9).join(","));
    expect(new Set(spots).size).toBe(9);
  });

  it("按地方分好", () => {
    const idol = (character_id: string, place: IdolOnMap["place"]) =>
      ({ character_id, place, trip: null, free: true }) as IdolOnMap;
    const grouped = byPlace([idol("mano", "office"), idol("hiori", "school"), idol("meguru", "office")]);
    expect(grouped.get("office")?.map((i) => i.character_id)).toEqual(["mano", "meguru"]);
    expect(grouped.get("school")?.map((i) => i.character_id)).toEqual(["hiori"]);
  });
});

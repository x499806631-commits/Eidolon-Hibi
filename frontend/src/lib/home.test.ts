import { describe, expect, it } from "vitest";

import { currentNavItem, isInShell, navDestination } from "@/lib/navShell";
import { PICTURE_LAYOUT, spotOf, THING_GOES_TO, timeOfDay } from "@/lib/home";

describe("制作人的家（GLOSSARY.md「制作人的家」）", () => {
  it("屋里的光跟着钟点：5–9 点早上，9–17 点白天，17–19 点傍晚，其余夜里", () => {
    const at = (hour: number) => timeOfDay(new Date(2026, 9, 23, hour, 30));
    expect([4, 5, 8, 9, 16, 17, 18, 19, 23, 0].map(at)).toEqual([
      "night", "morning", "morning", "day", "day", "evening", "evening", "night", "night", "night",
    ]);
  });

  it("屋里的东西通到应用里对应的地方，相框和窗户在屋里就办了", () => {
    expect(THING_GOES_TO.mailbox).toBe("/letters");
    expect(THING_GOES_TO.diary).toBe("/diary");
    expect(THING_GOES_TO.shelf).toBe("/keepsakes");
    expect(THING_GOES_TO.phone).toBe("/conversations");
    expect(THING_GOES_TO.drawer).toBe("/gifts");
    expect(THING_GOES_TO.frame).toBeNull();
    expect(THING_GOES_TO.window).toBeNull();
  });

  it("画好的屋子里，摆过的东西在用户摆的地方，没摆过的先沿墙摆着", () => {
    expect(spotOf({ diary: [0.2, 0.7] }, "diary")).toEqual([0.2, 0.7]);
    expect(spotOf({ diary: [0.2, 0.7] }, "phone")).toEqual(PICTURE_LAYOUT.phone);
  });

  it("家在导航外壳里，是导航的第一项", () => {
    expect(isInShell("/home")).toBe(true);
    expect(currentNavItem("/home")).toBe("home");
    expect(navDestination("home", "/gifts")).toBe("/home");
    expect(navDestination("home", "/home")).toBeNull();
  });
});

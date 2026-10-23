import { describe, expect, it } from "vitest";

import { TIERS, type Tier } from "@/lib/affinity";
import {
  affinityGroups,
  tierPreview,
  busiestParts,
  callTime,
  callTimeText,
  heatLevel,
  heatmapMonths,
  heatmapWeeks,
  mostCalled,
  mostTalked,
  portrait,
  statsSections,
  type Stats,
  type TierGroup,
} from "@/lib/stats";

/** 24 hours, all 0 but those given. */
function hours(counts: Record<number, number> = {}): number[] {
  return Array.from({ length: 24 }, (_, hour) => counts[hour] ?? 0);
}

/** The five tiers, from cold to intimate, each with those given in it. */
function tiers(given: Partial<Record<Tier, string[]>> = {}): TierGroup[] {
  return TIERS.map((tier) => ({
    tier,
    characters: given[tier] ?? [],
  }));
}

const none: Stats = {
  days_known: 3,
  visits: { current: 0, longest: 0 },
  all_hours: hours(),
  heatmap: { from: "2025-09-22", counts: Array(52 * 7 + 5).fill(0) },
  affinity: tiers({ trust: ["mano", "kogane"] }),
  achievements: { stages: 0, complete: 0 },
  range: {
    span: "all",
    turns: 0,
    by_hour: hours(),
    characters: [],
    calls: { count: 0, seconds: 0 },
    gifts: { given: 0, liked: 0, most: null },
  },
};

/** Someone who came, all of it in the range: a turn at 9. */
const came: Stats = {
  ...none,
  visits: { current: 1, longest: 1 },
  all_hours: hours({ 9: 1 }),
  range: { ...none.range, turns: 1, by_hour: hours({ 9: 1 }), characters: [known("mano", 1)] },
};

function known(character_id: string, turns: number, days_known = 10, call_seconds = 0) {
  return { character_id, turns, days_known, call_seconds };
}

/** Someone who called her, `seconds` long, and took no turn. */
function called(character_id: string, call_seconds: number) {
  return known(character_id, 0, 10, call_seconds);
}

describe("热力图", () => {
  it("一周一列，周一在上；最后一列只到今天", () => {
    const weeks = heatmapWeeks(none.heatmap);
    expect(weeks).toHaveLength(53);
    expect(weeks[0]).toHaveLength(7);
    expect(weeks[0][0].date).toBe("2025-09-22");
    expect(weeks[0][6].date).toBe("2025-09-28");
    expect(weeks[52].map((cell) => cell.date)).toEqual([
      "2026-09-21",
      "2026-09-22",
      "2026-09-23",
      "2026-09-24",
      "2026-09-25",
    ]);
  });

  it("每格带当天的数和深浅", () => {
    const counts = [...none.heatmap.counts];
    counts[1] = 2;
    counts[2] = 8;
    const [first] = heatmapWeeks({ ...none.heatmap, counts });
    expect(first.slice(0, 3)).toEqual([
      { date: "2025-09-22", month: 9, day: 22, count: 0, level: 0 },
      { date: "2025-09-23", month: 9, day: 23, count: 2, level: 1 },
      { date: "2025-09-24", month: 9, day: 24, count: 8, level: 4 },
    ]);
  });

  it("深浅分五级：没来是 0，来过的按占最多那天的比例分 1–4", () => {
    expect(heatLevel(0, 8)).toBe(0);
    expect(heatLevel(2, 8)).toBe(1);
    expect(heatLevel(3, 8)).toBe(2);
    expect(heatLevel(5, 8)).toBe(3);
    expect(heatLevel(8, 8)).toBe(4);
    expect(heatLevel(1, 100)).toBe(1); // 来过的再少也看得见
  });

  it("月份标在每月 1 号所在的那一列，挤的时候隔开", () => {
    const weeks = heatmapWeeks(none.heatmap);
    const months = heatmapMonths(weeks, 1);
    expect(months.get(1)).toBe("10月"); // 2025-10-01 在第二列
    expect(months.get(53 - 4)).toBe("9月"); // 2026-09-01
    expect(months.size).toBe(12);
    // 标签之间至少隔 5 列时，离上一个太近的不标
    expect([...heatmapMonths(weeks, 5).keys()]).toEqual([1, 10, 18, 27, 36, 44, 49]);
  });
});

describe("一句话画像", () => {
  it("按 0–6、6–12、12–18、18–24 四段里来得最多的那段", () => {
    expect(busiestParts(hours({ 1: 3, 13: 2 }))).toEqual(["night"]);
    expect(busiestParts(hours({ 6: 1, 11: 1, 20: 1 }))).toEqual(["morning"]);
    expect(busiestParts(hours({ 18: 1, 23: 4, 12: 4 }))).toEqual(["evening"]);
    expect(portrait(hours({ 2: 1 }))).toBe("常在深夜来");
  });

  it("两段并列时两段都说，按一天里的先后", () => {
    expect(portrait(hours({ 20: 2, 8: 2 }))).toBe("常在上午和晚上来");
  });

  it("三段以上并列时说什么时段都来", () => {
    expect(portrait(hours({ 1: 1, 8: 1, 20: 1 }))).toBe("什么时段都会来");
  });

  it("一次都没来过时没有画像", () => {
    expect(portrait(hours())).toBeNull();
  });
});

describe("聊得最多", () => {
  it("按回合数排前 5 位，条的长短按占第一位的比例", () => {
    const characters = [known("a", 3), known("b", 9), known("c", 1), known("d", 6), known("e", 2), known("f", 4)];
    const ranked = mostTalked(characters);
    expect(ranked.map((c) => c.character_id)).toEqual(["b", "d", "f", "a", "e"]);
    expect(ranked[0]).toEqual({ character_id: "b", turns: 9, days_known: 10, call_seconds: 0, share: 1 });
    expect(ranked[1].share).toBeCloseTo(6 / 9);
  });

  it("只列回合数大于 0 的；并列时照接口给的顺序（偶像顺序）", () => {
    const ranked = mostTalked([known("a", 0), known("b", 2), known("c", 2), known("d", 0)]);
    expect(ranked.map((c) => c.character_id)).toEqual(["b", "c"]);
    expect(mostTalked([known("a", 0)])).toEqual([]);
  });
});

describe("通话最久", () => {
  it("按接通时长排前 5 位，只列大于 0 的；并列时照接口给的顺序", () => {
    const characters = [called("a", 30), called("b", 0), called("c", 600), called("d", 30), known("e", 9)];
    const ranked = mostCalled([...characters, called("f", 90), called("g", 45), called("h", 1)]);
    expect(ranked.map((c) => c.character_id)).toEqual(["c", "f", "g", "a", "d"]);
    expect(ranked[0].share).toBe(1);
    expect(ranked[1].share).toBeCloseTo(90 / 600);
    expect(mostCalled([called("a", 0), known("b", 3)])).toEqual([]);
  });

  it("时长按分钟四舍五入，不满 1 分的算 1 分；满 60 分进 1 小时", () => {
    expect(callTime(0)).toEqual({ hours: 0, minutes: 0 });
    expect(callTime(5)).toEqual({ hours: 0, minutes: 1 });
    expect(callTime(89)).toEqual({ hours: 0, minutes: 1 });
    expect(callTime(90)).toEqual({ hours: 0, minutes: 2 });
    expect(callTime(3599)).toEqual({ hours: 1, minutes: 0 });
    expect(callTime(2 * 3600 + 5 * 60 + 10)).toEqual({ hours: 2, minutes: 5 });
  });

  it("排行的读数不满 1 小时只写分", () => {
    expect(callTimeText(12 * 60)).toBe("12 分");
    expect(callTimeText(3600)).toBe("1 小时 0 分");
  });
});

describe("好感度分组", () => {
  it("一档里人多时收起来只露前 5 位，只多 1 位时照样全露", () => {
    expect(tierPreview(3)).toBe(3);
    expect(tierPreview(6)).toBe(6);
    expect(tierPreview(7)).toBe(5);
    expect(tierPreview(30)).toBe(5);
  });

  it("只列有角色的档，从亲密到冷淡；档里照接口给的顺序（偶像顺序）", () => {
    expect(affinityGroups(tiers({ intimate: ["kogane"], cold: ["meguru", "mano"] }))).toEqual([
      { tier: "intimate", characters: ["kogane"] },
      { tier: "cold", characters: ["meguru", "mano"] },
    ]);
  });
});

describe("哪一块显示", () => {
  const nothing = {
    affinity: false,
    achievements: false,
    totals: false,
    giftsGiven: false,
    mostTalked: false,
    mostCalled: false,
    hours: false,
    gifts: false,
  };

  it("一个回合、一次送礼都没有时整页为空", () => {
    expect(statsSections(none)).toEqual({ isEmpty: true, visits: false, portrait: false, heatmap: false, ...nothing });
  });

  it("有来过就显示连续来访和画像；过去一年里有才显示热力图", () => {
    const old = { ...none, visits: { current: 0, longest: 2 }, all_hours: hours({ 9: 2 }) };
    expect(statsSections(old)).toMatchObject({ isEmpty: false, visits: true, portrait: true, heatmap: false });
    const counts = [...none.heatmap.counts];
    counts[100] = 2;
    expect(statsSections({ ...old, heatmap: { ...old.heatmap, counts } }).heatmap).toBe(true);
  });

  it("这段跨度里没来过时只剩总数", () => {
    const quiet = { ...came, range: { ...none.range, span: "30d" as const, characters: [known("mano", 0)] } };
    expect(statsSections(quiet)).toMatchObject({ totals: true, mostTalked: false, hours: false, gifts: false });
  });

  it("有回合才有聊得最多和分时段图，送过礼才有礼物；好感度开着时有好感度分组，送出的礼物没送过也写 0", () => {
    expect(statsSections(came)).toMatchObject({
      affinity: true,
      achievements: true,
      totals: true,
      giftsGiven: true,
      mostTalked: true,
      mostCalled: false,
      hours: true,
      gifts: false,
    });
    const gave = {
      ...came,
      range: { ...came.range, gifts: { given: 2, liked: 0, most: { character_id: "mano", given: 2 } } },
    };
    expect(statsSections(gave).gifts).toBe(true);
  });

  it("只打过电话、没有回合和送礼时不算空：有总数和通话最久，没有画像和连续来访", () => {
    const onlyCalled = {
      ...none,
      range: { ...none.range, characters: [called("mano", 12)], calls: { count: 1, seconds: 12 } },
    };
    expect(statsSections(onlyCalled)).toEqual({
      isEmpty: false,
      visits: false,
      portrait: false,
      heatmap: false,
      affinity: true,
      achievements: true,
      totals: true,
      giftsGiven: true,
      mostTalked: false,
      mostCalled: true,
      hours: false,
      gifts: false,
    });
  });

  it("这段跨度里只送了礼时有分时段图，没有聊得最多", () => {
    const onlyGave = {
      ...came,
      range: {
        ...came.range,
        turns: 0,
        characters: [known("mano", 0)],
        gifts: { given: 1, liked: 1, most: { character_id: "mano", given: 1 } },
      },
    };
    expect(statsSections(onlyGave)).toMatchObject({ mostTalked: false, hours: true, gifts: true });
  });

  it("好感度和礼物关掉时没有好感度分组、成就、礼物和送出的礼物", () => {
    const off = { ...came, affinity: null, achievements: null, range: { ...came.range, gifts: null } };
    expect(statsSections(off)).toMatchObject({
      affinity: false,
      achievements: false,
      totals: true,
      giftsGiven: false,
      gifts: false,
    });
  });
});

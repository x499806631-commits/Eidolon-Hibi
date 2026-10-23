import { describe, expect, it } from "vitest";

import { canShare, coversText, shareCard, shareFileName, siteAddress, type ShareCardContent } from "@/lib/shareCard";
import type { CharacterInRange, Stats } from "@/lib/stats";

/** 24 hours, all 0 but those given. */
function hours(counts: Record<number, number> = {}): number[] {
  return Array.from({ length: 24 }, (_, hour) => counts[hour] ?? 0);
}

function known(character_id: string, turns: number, call_seconds = 0): CharacterInRange {
  return { character_id, turns, days_known: 10, call_seconds };
}

/** Someone who came at night, in the past 30 days, and talked with three of them. */
const stats: Stats = {
  days_known: 40,
  visits: { current: 2, longest: 5 },
  all_hours: hours({ 1: 4, 20: 1 }),
  heatmap: { from: "2025-09-22", counts: Array(52 * 7 + 5).fill(0) },
  affinity: null,
  achievements: null,
  range: {
    span: "30d",
    turns: 12,
    by_hour: hours({ 1: 12 }),
    characters: [known("mano", 2), known("hiori", 7, 600), known("meguru", 3)],
    calls: { count: 2, seconds: 3900 },
    gifts: { given: 4, liked: 1, most: { character_id: "mano", given: 4 } },
  },
};

function card(of: Stats = stats, formOfAddress: string | null = "小林さん"): ShareCardContent {
  const made = shareCard(of, formOfAddress);
  if (!made) throw new Error("no card");
  return made;
}

describe("分享按钮", () => {
  it("当前跨度里有回合时才出现", () => {
    expect(canShare(stats)).toBe(true);
  });

  it("当前跨度里一个回合都没有时隐藏：只打过电话、只送过礼的也一样", () => {
    const quiet: Stats = {
      ...stats,
      range: { ...stats.range, turns: 0, characters: [known("mano", 0, 600)] },
    };
    expect(canShare(quiet)).toBe(false);
    expect(shareCard(quiet, null)).toBeNull();
  });
});

describe("分享图的内容", () => {
  it("主角是这个跨度里聊得最多的那位", () => {
    expect(card().hero).toBe("hiori");
  });

  it("前 3 位按回合数排", () => {
    const four: Stats = {
      ...stats,
      range: { ...stats.range, characters: [...stats.range.characters, known("kogane", 5)] },
    };
    expect(card(four).top.map((c) => c.character_id)).toEqual(["hiori", "kogane", "meguru"]);
  });

  it("不满三人时有几位列几位", () => {
    const two: Stats = { ...stats, range: { ...stats.range, characters: [known("mano", 1), known("hiori", 3)] } };
    expect(card(two).top.map((c) => c.character_id)).toEqual(["hiori", "mano"]);
  });

  it("写设了的称呼，前后空白去掉", () => {
    expect(card(stats, "  𠮷野さん ").address).toBe("𠮷野さん");
  });

  it("没设称呼时写「制作人」", () => {
    expect(card(stats, null).address).toBe("制作人");
    expect(card(stats, "   ").address).toBe("制作人");
  });

  it("跨度标签写当前的跨度", () => {
    expect(card({ ...stats, range: { ...stats.range, span: "1y" } }).span).toBe("近一年");
  });

  it("一句话画像按全部时间说，不跟着跨度", () => {
    const lately: Stats = { ...stats, range: { ...stats.range, by_hour: hours({ 20: 12 }) } };
    expect(card(lately).portrait).toBe("常在深夜来");
  });

  it("四个总数：回合、通话次数、通话时长（时、分）、送出的礼物", () => {
    expect(card().totals).toEqual([
      { label: "回合", parts: [{ value: 12 }] },
      { label: "通话", parts: [{ value: 2, unit: "次" }] },
      {
        label: "通话时长",
        parts: [
          { value: 1, unit: "小时" },
          { value: 5, unit: "分" },
        ],
      },
      { label: "送出的礼物", parts: [{ value: 4 }] },
    ]);
  });

  it("通话不满 1 小时只写分，为 0 照写 0", () => {
    const none: Stats = {
      ...stats,
      range: { ...stats.range, calls: { count: 0, seconds: 0 }, gifts: { given: 0, liked: 0, most: null } },
    };
    expect(card(none).totals.slice(1)).toEqual([
      { label: "通话", parts: [{ value: 0, unit: "次" }] },
      { label: "通话时长", parts: [{ value: 0, unit: "分" }] },
      { label: "送出的礼物", parts: [{ value: 0 }] },
    ]);
  });

  it("好感度和礼物关掉时没有「送出的礼物」，只剩三个总数", () => {
    const off: Stats = { ...stats, range: { ...stats.range, gifts: null } };
    expect(card(off).totals.map((t) => t.label)).toEqual(["回合", "通话", "通话时长"]);
  });
});

describe("总数的大数", () => {
  const withTotals = (turns: number, seconds: number, gifts: boolean) => ({
    ...stats,
    range: { ...stats.range, turns, calls: { count: 2, seconds }, gifts: gifts ? stats.range.gifts : null },
  });

  it("四个一排用 100，只剩三个时放大到 120", () => {
    expect(card(withTotals(293, 600, true)).totalSize).toBe(100);
    expect(card(withTotals(293, 600, false)).totalSize).toBe(120);
  });

  it("有一个放不下时整排一起缩小", () => {
    expect(card(withTotals(4821, 600, true)).totalSize).toBe(80);
    expect(card(withTotals(293, 12 * 3600 + 34 * 60, true)).totalSize).toBe(48);
    expect(card(withTotals(293, 12 * 3600 + 34 * 60, false)).totalSize).toBe(80);
  });
});

describe("文件名和网址", () => {
  it("文件名带本地的日期", () => {
    expect(shareFileName(new Date(2026, 9, 16, 23, 59))).toBe("eidolon-stats-2026-10-16.png");
    expect(shareFileName(new Date(2027, 0, 3, 0, 1))).toBe("eidolon-stats-2027-01-03.png");
  });

  it("卡上的网址去掉协议，二维码指向落地页", () => {
    expect(siteAddress("https://eidolon.example")).toEqual({
      shown: "eidolon.example",
      landing: "https://eidolon.example/",
    });
    expect(siteAddress("http://localhost:3000")).toEqual({
      shown: "localhost:3000",
      landing: "http://localhost:3000/",
    });
  });
});

describe("分享图嵌进去的字体切片", () => {
  const cjk = "U+4E00-4E0F, U+5927, U+1F300-1F320";

  it("切片里有卡上的字才要", () => {
    expect(coversText(cjk, "近一年")).toBe(true);
    expect(coversText(cjk, "大")).toBe(true);
    expect(coversText(cjk, "制作人")).toBe(false);
  });

  it("区间两端都算在内，大小写不论", () => {
    expect(coversText("u+4e00-4e0f", "丏")).toBe(true);
    expect(coversText("U+4E00-4E0F", "丐")).toBe(false);
  });

  it("增补平面的字按整个码点比", () => {
    expect(coversText(cjk, "🌀")).toBe(true);
    expect(coversText("U+D800-DFFF", "🌀")).toBe(false);
  });

  it("带问号的通配区间", () => {
    expect(coversText("U+4??", "Ѐӿ")).toBe(true);
    expect(coversText("U+4??", "Ԁ")).toBe(false);
  });

  it("没写 unicode-range 的字体全都要", () => {
    expect(coversText("", "abc")).toBe(true);
  });
});

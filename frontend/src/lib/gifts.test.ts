import { describe, expect, it } from "vitest";

import type { AffinityChange } from "@/lib/affinity";
import {
  chosenFirst,
  drawDots,
  drawNote,
  emptyNote,
  giftsFor,
  giftsHref,
  givenNote,
  resetNote,
  shownGifts,
  type GiftInfo,
  type Given,
} from "@/lib/gifts";

describe("抽奖台那句话", () => {
  it("平时：下次什么时候再发，最多攒几次", () => {
    expect(drawNote(3, 7, "明天 00:00")).toBe("明天 00:00 再发 1 次，最多攒 7 次");
  });

  it("攒满了：每天发的抽掉之前不会再发", () => {
    expect(drawNote(7, 7, "明天 00:00")).toBe("每天发的已攒满，抽掉之前不会再发");
  });

  it("每日任务给的让次数超过 7 次：说法同攒满", () => {
    expect(drawNote(9, 7, "明天 00:00")).toBe("每天发的已攒满，抽掉之前不会再发");
  });

  it("用完了：下次什么时候再发，不说「今天」（北京时间 0 点在别处可能是今天）", () => {
    expect(drawNote(0, 7, "今天 09:00")).toBe("已经抽完了，今天 09:00 再发 1 次");
  });
});

describe("抽奖台的小点", () => {
  it("攒了几次亮几个，不写「+n」", () => {
    expect(drawDots(3, 7)).toEqual({ lit: 3, more: null, label: "抽奖还有 3 次，最多攒 7 次" });
    expect(drawDots(7, 7)).toEqual({ lit: 7, more: null, label: "抽奖还有 7 次，最多攒 7 次" });
  });

  it("超过 7 次：7 个全亮，后面写超出几次", () => {
    expect(drawDots(9, 7)).toEqual({ lit: 7, more: "+2", label: "抽奖还有 9 次" });
  });
});

describe("还没有礼物时的说明", () => {
  it("有次数时叫人抽一次", () => {
    expect(emptyNote(1, "明天 00:00")).toBe("抽一次，抽到的礼物会放在这里。");
  });

  it("没有次数时说下次什么时候再发", () => {
    expect(emptyNote(0, "明天 01:00")).toBe("明天 01:00 会发一次抽奖，抽到的礼物会放在这里。");
  });
});

describe("重置卡条那句话", () => {
  it("有低于信任的角色：现在用会拉回几位", () => {
    expect(resetNote(2)).toBe("现在用会把 2 位拉回信任");
  });

  it("一位都没有：留着以后用", () => {
    expect(resetNote(0)).toBe("现在没有低于信任的角色，留着以后用。");
  });
});

describe("我的礼物按类别筛选", () => {
  const catalog: GiftInfo[] = [
    { id: "cake", name: "蛋糕", category: "food", rarity: "common", retired: false },
    { id: "tea", name: "红茶", category: "drink", rarity: "common", retired: false },
    { id: "cocoa", name: "热可可", category: "drink", rarity: "uncommon", retired: true },
    { id: "rose", name: "玫瑰", category: "flower", rarity: "rare", retired: false },
  ];
  const backpack = [
    { gift_id: "cake", count: 2 },
    { gift_id: "cocoa", count: 1 },
    { gift_id: "tea", count: 3 },
  ];

  it("全部：持有的每件，按背包的顺序，下架的照常列出", () => {
    expect(shownGifts(backpack, catalog, "all").map((h) => [h.gift.id, h.count])).toEqual([
      ["cake", 2],
      ["cocoa", 1],
      ["tea", 3],
    ]);
  });

  it("一类：只列这一类的", () => {
    expect(shownGifts(backpack, catalog, "drink").map((h) => h.gift.id)).toEqual(["cocoa", "tea"]);
  });

  it("这一类一件都没有时是空的", () => {
    expect(shownGifts(backpack, catalog, "flower")).toEqual([]);
  });

  it("目录里没有的不列", () => {
    expect(shownGifts([{ gift_id: "gone", count: 1 }], catalog, "all")).toEqual([]);
  });
});

describe("送出后底部提示的那句话", () => {
  // 时刻按东京时区写，测试不随跑测试的机器变。
  const now = new Date("2026-10-13T20:00:00+09:00");
  const up = (change: Partial<AffinityChange>): AffinityChange => ({
    up: true,
    tier_changed: false,
    tier: "trust",
    cooldown: false,
    thawed: false,
    ...change,
  });
  const given = (change: AffinityChange | null, cooldown_until: string | null = null): Given => ({
    conversation_id: "c1",
    change,
    affinity: { tier: change?.tier ?? "cold", cooling: cooldown_until !== null, cooldown_until },
    left: 0,
    rewarded: null,
  });
  const note = (g: Given) => givenNote(g, "凛世", now, "Asia/Tokyo");

  it("有变化：就是变化胶囊的话", () => {
    expect(note(given(up({ tier_changed: true, tier: "close" })))).toBe("和凛世更近了一步：亲近");
  });

  it("冷却中送了还没解除：写解除时刻，和对话页的写法一样", () => {
    expect(note(given(null, "2026-10-14T05:30:00+09:00"))).toBe("凛世还没消气，明天 05:30 就会愿意说话了");
  });

  it("到顶了没有变化：只写已经送到", () => {
    expect(note(given(null))).toBe("已经送给凛世");
  });
});

describe("从对话页跳到礼物页", () => {
  const query = (href: string) => Object.fromEntries(new URL(href, "http://x.invalid").searchParams);

  it("地址只带她的角色 id，礼物页读回来", () => {
    const href = giftsHref("rinze");
    expect(href).toBe("/gifts?to=rinze");
    expect(giftsFor(query(href))).toEqual({ to: "rinze" });
  });

  it("没有 to，或者 to 是空的，都当作没有预选", () => {
    expect(giftsFor({ conversation: "c1" })).toBeNull();
    expect(giftsFor({ to: "" })).toBeNull();
  });
});

describe("送给谁的顺序", () => {
  const characters = [{ id: "mano" }, { id: "hiori" }, { id: "rinze" }];

  it("预选的她排第一，其余照原来的顺序", () => {
    expect(chosenFirst(characters, "rinze").map((c) => c.id)).toEqual(["rinze", "mano", "hiori"]);
  });

  it("预选的不在里面，照原来的顺序", () => {
    expect(chosenFirst(characters, "gone").map((c) => c.id)).toEqual(["mano", "hiori", "rinze"]);
  });
});

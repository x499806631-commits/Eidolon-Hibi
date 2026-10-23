import { afterEach, describe, expect, it, vi } from "vitest";

import {
  achievementRow,
  achievementsSeenUpTo,
  completeRow,
  keepAchievementsSeenUpTo,
  latestUnlock,
  unseenRows,
  withUnlocked,
  type Achievement,
  type HerAchievements,
} from "@/lib/achievements";

const AT = "2026-09-25T12:00:00+00:00";

/** 一个成就：门槛 `goals`（第 n 阶段给 n 次），已经进到 `progress`，前 `unlocked` 个阶段解锁了。 */
function achievement(id: string, progress: Achievement["progress"], goals: (number | string)[], unlocked = 0): Achievement {
  return {
    id,
    progress,
    stages: goals.map((goal, i) => ({ goal, draws: i + 1, unlocked_at: i < unlocked ? AT : null })),
  } as Achievement;
}

describe("成就的一行", () => {
  it("回合：名字、阶段点、下一个阶段的进度和它的奖励", () => {
    const row = achievementRow(achievement("turns", 420, [10, 100, 300, 1000, 3000], 3));

    expect(row).toEqual({
      id: "turns",
      name: "和她的回合",
      stages: [true, true, true, false, false],
      isAllUnlocked: false,
      progress: "420 个回合 / 1000 个回合",
      reward: "抽奖 ×4",
    });
  });

  it("一个都没解锁：下一个是第一阶段，抽奖 ×1", () => {
    const row = achievementRow(achievement("gifts", 0, [1, 10, 30, 100]));

    expect(row.stages).toEqual([false, false, false, false]);
    expect(row.progress).toBe("0 件 / 1 件");
    expect(row.reward).toBe("抽奖 ×1");
  });

  it("全部解锁：写「全部解锁」，右边不写奖励", () => {
    const row = achievementRow(achievement("gifts", 140, [1, 10, 30, 100], 4));

    expect(row.isAllUnlocked).toBe(true);
    expect(row.progress).toBe("全部解锁");
    expect(row.reward).toBeNull();
  });

  it("下一个阶段按解锁了几个算，不按进度：门槛调低以后还没解锁的照样是下一个", () => {
    const row = achievementRow(achievement("turns", 120, [10, 100, 300], 1));

    expect(row.progress).toBe("120 个回合 / 100 个回合");
    expect(row.reward).toBe("抽奖 ×2");
  });

  it.each([
    [0, 600, "0 分钟 / 10 分钟"],
    [11520, 18000, "3 小时 12 分 / 5 小时"],
    [7200, 18000, "2 小时 / 5 小时"],
    [3599, 3600, "59 分钟 / 1 小时"],
  ])("通话 %i 秒、下一个 %i 秒：%s", (progress, goal, text) => {
    const row = achievementRow(achievement("call_time", progress, [goal]));

    expect(row.name).toBe("和她通话");
    expect(row.progress).toBe(text);
  });

  it("档位：写「下一个：亲密」，不写进度", () => {
    const row = achievementRow(achievement("tier", "close", ["close", "intimate"], 1));

    expect(row.name).toBe("她的好感度到过");
    expect(row.progress).toBe("下一个：亲密");
    expect(row.reward).toBe("抽奖 ×2");
  });

  it.each([
    ["characters", "往来过的角色", 5, 10, "5 位 / 10 位"],
    ["intimate_characters", "到过亲密的角色", 0, 1, "0 位 / 1 位"],
    ["days", "来过的天数", 12, 30, "12 天 / 30 天"],
    ["draws", "抽过的奖", 37, 50, "37 次 / 50 次"],
  ])("合起来的 %s：%s，%s", (id, name, progress, goal, text) => {
    const row = achievementRow(achievement(id, progress, [goal]));

    expect(row.name).toBe(name);
    expect(row.progress).toBe(text);
  });

  it("不认识的成就照样列出，名字用 id，进度写数字", () => {
    const row = achievementRow(achievement("photos", 2, [5]));

    expect(row.name).toBe("photos");
    expect(row.progress).toBe("2 / 5");
  });
});

describe("集齐那一行", () => {
  it("没集齐：空圈、「集齐」、「重置卡 ×1」", () => {
    expect(completeRow({ completed_at: null, reset_cards: 1 })).toEqual({
      isComplete: false,
      name: "集齐",
      reward: "重置卡 ×1",
    });
  });

  it("重置卡已经给过、加了阶段又没集齐：右边不写", () => {
    expect(completeRow({ completed_at: null, reset_cards: 0 })).toEqual({ isComplete: false, name: "集齐", reward: null });
  });

  it("集齐了：勾、「集齐了」、右边不写", () => {
    expect(completeRow({ completed_at: AT, reset_cards: 1 })).toEqual({
      isComplete: true,
      name: "集齐了",
      reward: null,
    });
  });
});

/** 这台浏览器的 localStorage，放在内存里。 */
function storage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  });
  return items;
}

describe("这台设备上看过的成就", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("按用户、按范围分开记，读回来就是记下的", () => {
    storage();

    keepAchievementsSeenUpTo("seen-1", "hikari", "2026-10-20T08:00:00+00:00");
    keepAchievementsSeenUpTo("seen-1", "all", "2026-10-19T08:00:00+00:00");
    keepAchievementsSeenUpTo("seen-2", "hikari", "2026-10-18T08:00:00+00:00");

    expect(achievementsSeenUpTo("seen-1", "hikari")).toBe("2026-10-20T08:00:00+00:00");
    expect(achievementsSeenUpTo("seen-1", "all")).toBe("2026-10-19T08:00:00+00:00");
    expect(achievementsSeenUpTo("seen-2", "hikari")).toBe("2026-10-18T08:00:00+00:00");
    expect(achievementsSeenUpTo("seen-2", "all")).toBeNull();
  });
});

/** 她的成就：回合前两个阶段、礼物第一阶段各在什么时刻解锁，集齐了没有。 */
function hers({
  turns = [null, null],
  gifts = null,
  completedAt = null,
}: {
  turns?: (string | null)[];
  gifts?: string | null;
  completedAt?: string | null;
} = {}): HerAchievements {
  return {
    achievements: [
      { id: "turns", progress: 120, stages: turns.map((at, i) => ({ goal: [10, 100][i], draws: i + 1, unlocked_at: at })) },
      { id: "gifts", progress: 3, stages: [{ goal: 1, draws: 1, unlocked_at: gifts }] },
    ],
    complete: { completed_at: completedAt, reset_cards: 1 },
  };
}

describe("她的成就上的点", () => {
  const before = "2026-10-20T08:00:00+00:00";
  const after = "2026-10-20T09:00:00+00:00";

  it("最近解锁的时刻是所有阶段和集齐里最晚的那个；一个都没解锁是 null", () => {
    expect(latestUnlock(hers({ turns: [before, after], gifts: before }))).toBe(after);
    expect(latestUnlock(hers({ turns: [before, before], gifts: before, completedAt: after }))).toBe(after);
    expect(latestUnlock(hers())).toBeNull();
  });

  it("面板里带点的是解锁晚于打开前看过的那几行，连同集齐", () => {
    const her = hers({ turns: [before, after], gifts: before, completedAt: after });

    expect(unseenRows(her, before)).toEqual(new Set(["turns", "complete"]));
    expect(unseenRows(her, after)).toEqual(new Set());
    expect(unseenRows(her, null)).toEqual(new Set(["turns", "gifts", "complete"]));
  });
});

/** 合起来的四个里的两个：往来过的角色第一阶段、抽过的奖前两个阶段各在什么时刻解锁。 */
function ofAll({ characters = null, draws = [null, null] }: { characters?: string | null; draws?: (string | null)[] } = {}) {
  return [
    { id: "characters", progress: 4, stages: [{ goal: 3, draws: 1, unlocked_at: characters }, { goal: 10, draws: 2, unlocked_at: null }] },
    { id: "draws", progress: 9, stages: draws.map((at, i) => ({ goal: [10, 50][i], draws: i + 1, unlocked_at: at })) },
  ] satisfies Achievement[];
}

describe("礼物页那一小节", () => {
  const before = "2026-10-20T08:00:00+00:00";
  const after = "2026-10-20T09:00:00+00:00";

  it("送礼、抽奖返回里带着合起来的阶段：当场标成解锁，时刻是这次到账的，进度至少到它的门槛", () => {
    const rewarded = {
      draws: 1,
      reset_cards: 0,
      daily_tasks: [],
      achievements: [
        { achievement: "draws", character_id: null, stage: 1, draws: 1 },
        { achievement: "turns", character_id: "mano", stage: 1, draws: 1 },
      ],
      completed: [],
      rewarded_at: after,
    };

    const [characters, draws] = withUnlocked(ofAll({ characters: before }), rewarded);

    expect(characters).toEqual(ofAll({ characters: before })[0]);
    expect(draws.progress).toBe(10);
    expect(draws.stages.map((s) => s.unlocked_at)).toEqual([after, null]);
    expect(achievementRow(draws).progress).toBe("10 次 / 50 次");
  });

  it("这次没有到账时原样不动", () => {
    const achievements = ofAll();
    expect(withUnlocked(achievements, null)).toBe(achievements);
  });
});

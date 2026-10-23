import { describe, expect, it } from "vitest";

import {
  achievementTiles,
  affinityRows,
  countTicks,
  dailyTaskTiles,
  durationRange,
  formatCount,
  formatCacheHitRate,
  formatDuration,
  formatFetchedAt,
  formatMs,
  formatPercentiles,
  formatShare,
  formatUsage,
  foldedCount,
  healthRowId,
  healthRowName,
  healthStatus,
  healthSummary,
  isDashboardRange,
  axisLabels,
  dashboardHref,
  dashboardTabs,
  labelledStretches,
  rangeFromQuery,
  reachOutTiles,
  splitZeroRows,
  stretchLabel,
  stretchSpan,
  strippedText,
  tabFromQuery,
} from "@/lib/dashboard";

/** An ISO time at a local hour, as the backend sends a stretch's start. */
const local = (month: number, day: number, hour: number) =>
  new Date(2026, month - 1, day, hour).toISOString();

describe("isDashboardRange", () => {
  it("knows only the ranges the backend serves", () => {
    for (const [value, known] of [["24h", true], ["1y", false]] as const)
      expect(isDashboardRange(value), value).toBe(known);
  });
});

describe("rangeFromQuery, tabFromQuery and dashboardHref", () => {
  it("takes a served range from the address and falls back to 24 hours", () => {
    expect(rangeFromQuery("7d")).toBe("7d");
    for (const value of [undefined, "1y", ["7d", "30d"]]) expect(rangeFromQuery(value), String(value)).toBe("24h");
  });

  it("takes a tab from the address and falls back to the overview", () => {
    expect(tabFromQuery("cost")).toBe("cost");
    for (const value of [undefined, "health", ["usage", "cost"]]) expect(tabFromQuery(value), String(value)).toBe("overview");
  });

  it("leaves the default range and tab out of the address", () => {
    expect(dashboardHref("/admin", "24h", "overview")).toBe("/admin");
    expect(dashboardHref("/admin", "90d", "overview")).toBe("/admin?range=90d");
    expect(dashboardHref("/admin", "24h", "usage")).toBe("/admin?tab=usage");
    expect(dashboardHref("/admin", "7d", "cost")).toBe("/admin?range=7d&tab=cost");
  });
});

describe("formatFetchedAt", () => {
  it("gives the clock today and the day as well on another day", () => {
    const now = new Date(2026, 9, 8, 16, 30);
    expect(formatFetchedAt(new Date(2026, 9, 8, 9, 5), now)).toBe("09:05");
    expect(formatFetchedAt(new Date(2026, 9, 7, 23, 59), now)).toBe("10月7日 23:59");
  });
});

describe("number formats", () => {
  it("counts are comma'd, in 万 from ten thousand on, and in 亿 from a hundred million on", () => {
    const cases: [number, string][] = [
      [0, "0"],
      [1284, "1,284"],
      [12_900, "1.3万"],
      [40_000, "4万"],
      [99_940_000, "9994万"],
      [99_999_600, "1亿"],
      [122_380_000, "1.22亿"],
      [149_063_000, "1.49亿"],
      [210_000_000, "2.1亿"],
      [300_000_000, "3亿"],
    ];
    for (const [n, shown] of cases) expect(formatCount(n), String(n)).toBe(shown);
  });

  it("shares are to one decimal, a sliver is never 0, and of nothing there is none", () => {
    const cases: [number, number, string][] = [
      [1, 4, "25%"], [1, 3, "33.3%"], [0, 5, "0%"], [1, 5000, "<0.1%"], [0, 0, "—"],
    ];
    for (const [part, whole, shown] of cases) expect(formatShare(part, whole), `${part}/${whole}`).toBe(shown);
  });

  it("durations are in ms under a second, then in seconds", () => {
    const cases: [number | null, string][] = [[null, "—"], [842.4, "842 ms"], [1250, "1.3 s"], [3000, "3 s"]];
    for (const [ms, shown] of cases) expect(formatMs(ms), String(ms)).toBe(shown);
  });
});

describe("healthStatus", () => {
  it("goes by the error rate, and says nothing without calls", () => {
    const cases: [number, number, ReturnType<typeof healthStatus>][] = [
      [0, 0, "idle"], [100, 0.01, "ok"], [100, 0.05, "degraded"], [100, 0.5, "failing"],
    ];
    for (const [calls, rate, status] of cases)
      expect(healthStatus({ calls, error_rate: rate }), `${calls} ${rate}`).toBe(status);
  });
});

describe("healthSummary", () => {
  const row = (service: string, calls: number, error_rate: number) =>
    ({ service, own_key: false, calls, error_rate, errors_by_kind: {}, latency_ms: { p50: null, p95: null }, usage: [], usage_unknown: 0 }) as unknown as Parameters<typeof healthSummary>[0][number];

  it("is idle without calls, ok when every called service is fine", () => {
    expect(healthSummary([]).kind).toBe("idle");
    expect(healthSummary([row("tts", 0, 0)]).kind).toBe("idle");
    expect(healthSummary([row("tts", 100, 0.01), row("stt", 0, 0)]).kind).toBe("ok");
  });

  it("lists the troubled services, failing first, then by error rate", () => {
    const summary = healthSummary([
      row("tts", 100, 0.05), row("stt", 100, 0.5), row("rerank", 100, 0.08), row("judge", 100, 0),
    ]);
    expect(summary.kind).toBe("problems");
    if (summary.kind !== "problems") return;
    expect(summary.worst).toBe("failing");
    expect(summary.rows.map((r) => r.service)).toEqual(["stt", "rerank", "tts"]);
  });
});

describe("the health table's rows", () => {
  type Row = Parameters<typeof healthRowId>[0];
  const base = { own_key: false, calls: 3, errors: 0, error_rate: 0, errors_by_kind: {}, latency_ms: { p50: 900, p95: 1500 }, usage: [], usage_unknown: 0 };
  const llm = (own_key = false): Row =>
    ({ ...base, service: "chat_llm", own_key, first_token_ms: { p50: 842.4, p95: 1250 } }) as Row;
  const tts = { ...base, service: "tts" } as Row;

  it("are one for each service on the deployer's keys and one on users' own", () => {
    const rows = [llm(), llm(true), tts];
    expect(new Set(rows.map(healthRowId)).size).toBe(rows.length);
  });

  it("say how soon an LLM's first word came, and nothing of it for other services", () => {
    expect(formatPercentiles(llm().first_token_ms)).toBe("842 ms / 1.3 s");
    expect(formatPercentiles(tts.first_token_ms)).toBe("—");
  });

  it("say how much of an LLM's input hit the cache, and nothing of it for other services", () => {
    expect(formatCacheHitRate({ ...llm(), cache_hit_rate: 0.7234 })).toBe("72.3%");
    expect(formatCacheHitRate({ ...llm(), cache_hit_rate: 0 })).toBe("0%");
    expect(formatCacheHitRate({ ...llm(), cache_hit_rate: null })).toBe("—");
    expect(formatCacheHitRate(tts)).toBe("—");
  });

  it("are named by service and whose keys", () => {
    expect(healthRowName(tts)).toBe("语音合成");
    expect(healthRowName(llm())).toBe("扮演角色的 LLM");
    expect(healthRowName(llm(true))).toBe("扮演角色的 LLM（自带密钥）");
  });

  it("say from how many photos tags were stripped, by part of the line, for image generation alone", () => {
    const image = { ...base, service: "image" } as Row;
    expect(strippedText({ ...image, stripped_photos: 2, stripped: { nudity: 2, appearance: 1 } })).toBe(
      "2 张剥掉了 tag：裸露 2、外貌 1",
    );
    expect(strippedText({ ...image, stripped_photos: 0, stripped: {} })).toBeNull();
    expect(strippedText(tts)).toBeNull();
  });
});

describe("splitZeroRows", () => {
  it("keeps the counted rows in order and sets the zeros apart", () => {
    const { counted, zero } = splitZeroRows([
      { id: "a", count: 3 }, { id: "b", count: 0 }, { id: "c", count: 1 }, { id: "d", count: 0 },
    ]);
    expect(counted.map((r) => r.id)).toEqual(["a", "c"]);
    expect(zero.map((r) => r.id)).toEqual(["b", "d"]);
  });
});

describe("formatUsage", () => {
  it("shows input and output where billed apart, input alone otherwise, and the calls that did not say", () => {
    expect(formatUsage([{ unit: "tokens", input: 12_345, output: 678, cached: 9000 }], 0)).toBe(
      "输入 1.2万 · 输出 678 tokens",
    );
    expect(formatUsage([{ unit: "tokens", input: 300, output: null }], 2)).toBe("300 tokens；2 次未知");
    expect(formatUsage([], 0)).toBe("—");
    // 单位译成中文，和花费表一致，不再是后端的 bytes、seconds。
    expect(formatUsage([{ unit: "bytes", input: 770, output: null }], 0)).toBe("770 字节");
    expect(formatUsage([{ unit: "seconds", input: 89, output: null }], 0)).toBe("89 秒");
  });
});

describe("stretches of the trend", () => {
  it("are labelled by hour, by day, or by day at midnight for six-hour stretches", () => {
    expect(stretchLabel(local(10, 7, 14), "hour")).toBe("14:00");
    expect(stretchLabel(local(10, 7, 6), "6h")).toBe("06:00");
    expect(stretchLabel(local(10, 7, 0), "6h")).toBe("10月7日");
    expect(stretchLabel(local(10, 7, 0), "day")).toBe("10月7日");
  });

  it("span their whole length in the tooltip", () => {
    expect(stretchSpan(local(10, 7, 14), "hour")).toBe("10月7日 14:00–15:00");
    expect(stretchSpan(local(10, 7, 18), "6h")).toBe("10月7日 18:00–00:00");
    expect(stretchSpan(local(10, 7, 0), "day")).toBe("10月7日");
  });

  it("label six-hour stretches by day where the day changes, so a week never reads 12:00 throughout", () => {
    // 28 six-hour stretches starting at 02:00 local, as UTC-aligned stretches do at UTC+8.
    const starts = Array.from({ length: 28 }, (_, i) => local(10, 1, 2 + i * 6));
    const every4 = axisLabels(starts, "6h", labelledStretches(28, 7));
    expect([...every4.values()]).toEqual(["10月1日", "10月2日", "10月3日", "10月4日", "10月5日", "10月6日", "10月7日"]);
    const every2 = axisLabels(starts, "6h", labelledStretches(28, 14));
    expect([...every2.values()].slice(0, 3)).toEqual(["10月1日", "20:00", "10月2日"]);
    expect(axisLabels(starts, "hour", new Set([0])).get(0)).toBe("02:00");
  });

  it("label at most so many, always the latest", () => {
    expect([...labelledStretches(24, 6)].sort((a, b) => a - b)).toEqual([3, 7, 11, 15, 19, 23]);
    expect(labelledStretches(4, 6).size).toBe(4);
  });
});

describe("countTicks", () => {
  it("are whole, clean steps from 0 past the largest count", () => {
    const cases: [number, number[]][] = [
      [0, [0, 1]],
      [3, [0, 1, 2, 3]],
      [37, [0, 10, 20, 30, 40]],
      [1200, [0, 500, 1000, 1500]],
    ];
    for (const [max, ticks] of cases) expect(countTicks(max), String(max)).toEqual(ticks);
  });
});

describe("call durations", () => {
  it("a duration is in seconds under a minute, then minutes, then hours", () => {
    const cases: [number | null, string][] = [
      [null, "—"],
      [0.4, "0 秒"],
      [45, "45 秒"],
      [125, "2 分 5 秒"],
      [600, "10 分"],
      [3725, "1 小时 2 分"],
    ];
    for (const [seconds, shown] of cases) expect(formatDuration(seconds), String(seconds)).toBe(shown);
  });

  it("each stretch of the distribution is named by its bounds", () => {
    const cases: [number, number | null, string][] = [
      [0, 60, "1 分钟以内"],
      [60, 180, "1–3 分钟"],
      [1800, null, "30 分钟以上"],
    ];
    for (const [from, to, shown] of cases) expect(durationRange(from, to), `${from}-${to}`).toBe(shown);
  });
});

describe("affinityRows", () => {
  const row = (id: string, judged: number, raised_share: number | null, lowered_share: number | null) => ({
    character_id: id,
    name: id,
    judged,
    raised_share,
    lowered_share,
    cooldowns: 2,
    she_ended: 3,
  });

  it("tells the shares raised and lowered as percentages, dashes for too few, and on a phone ends the row on them", () => {
    const [many, few, none] = affinityRows([
      row("mano", 1200, 0.4, 0.125),
      row("meguru", 3, null, null),
      row("rinze", 20, 0, 0),
    ]);
    expect([many.raised, many.lowered]).toEqual(["40%", "12.5%"]);
    expect([few.raised, few.lowered]).toEqual(["—", "—"]);
    expect([none.raised, none.lowered]).toEqual(["0%", "0%"]);
    expect(many.judged).toBe("1,200");
    // On a phone, each row ends on the two shares, the rest beneath.
    expect(many.end).toBe("加分 40% · 减分 12.5%");
    expect(few.end).toBe("加分 — · 减分 —");
    expect(many.details).toBe("判断过 1,200 个回合 · 冷却 2 · 挂断或结束对话 3");
  });
});

describe("reachOutTiles", () => {
  it("are none where the section is empty", () => {
    expect(reachOutTiles(null)).toEqual([]);
  });

  it("count those reached out and failed, and tell the share answered, a dash of none reached out", () => {
    expect(reachOutTiles({ succeeded: 1200, failed: 3, answered_share: 0.25 }).map((tile) => [tile.label, tile.value])).toEqual([
      ["成功", "1,200"],
      ["失败", "3"],
      ["被回了", "25%"],
    ]);
    expect(reachOutTiles({ succeeded: 0, failed: 2, answered_share: null })[2].value).toBe("—");
  });
});

describe("foldedCount", () => {
  it("shows the first five of a long list, and all of a list that would hide just one", () => {
    expect([6, 7].map(foldedCount)).toEqual([6, 5]);
  });
});

describe("dailyTaskTiles", () => {
  it("are none where the section is empty", () => {
    expect(dailyTaskTiles(null)).toEqual([]);
  });

  it("count each task done, in the order of the gifts page", () => {
    expect(dailyTaskTiles({ one_turn: 1200, one_gift: 3, ten_turns: 0 }).map((tile) => [tile.label, tile.value])).toEqual([
      ["一个回合", "1,200"],
      ["送一件礼", "3"],
      ["十个回合", "0"],
    ]);
  });
});

describe("achievementTiles", () => {
  it("are none where the section is empty", () => {
    expect(achievementTiles(null)).toEqual([]);
  });

  it("count the stages unlocked, the draws they gave and the reset cards", () => {
    expect(achievementTiles({ stages: 1200, draws: 3400, reset_cards: 0 }).map((tile) => [tile.label, tile.value])).toEqual([
      ["解锁的阶段", "1,200"],
      ["发出的抽奖次数", "3,400"],
      ["重置卡", "0"],
    ]);
  });
});

describe("dashboardTabs", () => {
  const affinity = { cooldowns: 0, cooled_users: 0, out_of_line: 0, manipulation: 0, levels: [0, 0, 0, 0, 0], characters: [] };
  const tasks = { one_turn: 0, one_gift: 0, ten_turns: 0 };

  it("have「好感度」where affinity is on, or daily tasks were done in the range", () => {
    expect(dashboardTabs({ affinity, daily_tasks: tasks })).toEqual(["overview", "usage", "affinity", "cost"]);
    expect(dashboardTabs({ affinity: null, daily_tasks: tasks })).toEqual(["overview", "usage", "affinity", "cost"]);
  });

  it("have none where both are empty", () => {
    expect(dashboardTabs({ affinity: null, daily_tasks: null })).toEqual(["overview", "usage", "cost"]);
  });
});

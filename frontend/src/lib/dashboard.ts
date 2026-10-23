// The admin console's dashboard (GLOSSARY「看板」): what the backend sums up from the
// activity records, and how the page words and lays out its numbers. Nothing is summed up
// here; the backend does all of that.

import { copy } from "@/i18n";
import type { Cost } from "@/lib/adminPrices";
import { api, json } from "@/lib/api";

export const DASHBOARD_RANGES = ["24h", "7d", "30d", "90d"] as const;

export type DashboardRange = (typeof DASHBOARD_RANGES)[number];

export function isDashboardRange(value: string): value is DashboardRange {
  return (DASHBOARD_RANGES as readonly string[]).includes(value);
}

/** The range a link asks for in `?range=`; 24 hours when it asks for none or one not served. */
export function rangeFromQuery(value: string | string[] | undefined): DashboardRange {
  return typeof value === "string" && isDashboardRange(value) ? value : "24h";
}

/** The dashboard's groups, one tab each: the overview (tiles, health, backup) first, the default. */
export const DASHBOARD_TABS = ["overview", "usage", "affinity", "cost"] as const;

export type DashboardTab = (typeof DASHBOARD_TABS)[number];

/** The tabs a dashboard has:「好感度」only where affinity is on, or daily tasks were done in the
 * range while it was. */
export function dashboardTabs(board: Pick<Dashboard, "affinity" | "daily_tasks">): DashboardTab[] {
  return DASHBOARD_TABS.filter((name) => name !== "affinity" || board.affinity !== null || board.daily_tasks !== null);
}

export function isDashboardTab(value: string): value is DashboardTab {
  return (DASHBOARD_TABS as readonly string[]).includes(value);
}

/** The tab a link asks for in `?tab=`; the overview when it asks for none or one not there. */
export function tabFromQuery(value: string | string[] | undefined): DashboardTab {
  return typeof value === "string" && isDashboardTab(value) ? value : "overview";
}

/** The address of the dashboard showing `range` under `tab`: the defaults, 24 hours and the
 * overview, need no query. */
export function dashboardHref(pathname: string, range: DashboardRange, tab: DashboardTab): string {
  const query = new URLSearchParams();
  if (range !== "24h") query.set("range", range);
  if (tab !== "overview") query.set("tab", tab);
  const search = query.toString();
  return search ? `${pathname}?${search}` : pathname;
}

export type ServiceName =
  | "chat_llm"
  | "memory_llm"
  | "tts"
  | "stt"
  | "judge"
  | "embedding"
  | "rerank"
  | "image"
  | "web_search";

export type ErrorKind =
  | "timeout"
  | "connection"
  | "rate_limited"
  | "unauthorized"
  | "refused"
  | "server_error"
  | "unreadable"
  | "other";

/** Which part of the line a tag stripped from her photo tag crossed (ADR-0023). */
export type Crossing =
  | "nudity"
  | "underwear"
  | "exposure"
  | "bathing"
  | "pose"
  | "expression"
  | "appearance"
  | "style"
  | "people";

/**
 * The page head's one line on health: "ok" when every service that was called kept its errors
 * low, "idle" when nothing was called, else the troubled rows, the worst first (失败多的在前，
 * 同档按出错率).
 */
export function healthSummary(
  health: ServiceHealth[],
): { kind: "ok" | "idle" } | { kind: "problems"; worst: "degraded" | "failing"; rows: ServiceHealth[] } {
  const rank = (row: ServiceHealth) => (healthStatus(row) === "failing" ? 2 : healthStatus(row) === "degraded" ? 1 : 0);
  const rows = health.filter((row) => rank(row) > 0).sort((a, b) => rank(b) - rank(a) || b.error_rate - a.error_rate);
  if (rows.length > 0) return { kind: "problems", worst: rank(rows[0]) === 2 ? "failing" : "degraded", rows };
  return { kind: health.some((row) => row.calls > 0) ? "ok" : "idle" };
}

/** A breakdown's rows with a count, in their order, and the labels of those at 0, which the
 * table folds into one line rather than a row each. */
export function splitZeroRows<T extends { count: number }>(rows: T[]): { counted: T[]; zero: T[] } {
  return { counted: rows.filter((row) => row.count > 0), zero: rows.filter((row) => row.count <= 0) };
}

/** What a service's calls were billed for in one unit, and for an LLM, of the input, how much hit the
 * provider's prompt cache; null where no call said. */
export type Usage = { unit: string; input: number | null; output: number | null; cached?: number | null };

/** A p50 and a p95 in milliseconds; null where nothing was timed. */
export type Percentiles = { p50: number | null; p95: number | null };

/**
 * One service's calls, on the deployer's keys or on users' own (`own_key`); an LLM's, at
 * whatever reasoning effort, with how soon the first word came.
 */
export type ServiceHealth = {
  service: ServiceName;
  own_key: boolean;
  calls: number;
  errors: number;
  error_rate: number;
  errors_by_kind: Partial<Record<ErrorKind, number>>;
  latency_ms: Percentiles;
  usage: Usage[];
  /** Calls the service did not say what it billed for. */
  usage_unknown: number;
  /** An LLM's alone: from the request to its first text or tool call; thinking is neither. */
  first_token_ms?: Percentiles;
  /** An LLM's alone: of all its input, the share read from the prompt cache, a call that did not say counting
   * as none; null where no call said what its input was. */
  cache_hit_rate?: number | null;
  /** Image generation's alone: from how many photos tags were stripped for crossing the line. */
  stripped_photos?: number;
  /** Image generation's alone: how many tags were stripped, by which part of the line each crossed. */
  stripped?: Partial<Record<Crossing, number>>;
};

/** A health row's key: a service has a row for the deployer's keys and one for users' own. */
export function healthRowId(row: ServiceHealth): string {
  return `${row.service}:${row.own_key}`;
}

/** A service's name: 「扮演角色的 LLM」. */
export function serviceName(row: ServiceHealth): string {
  return copy.admin.dashboard.health.services[row.service] ?? row.service;
}

/** 「842 ms / 1.3 s」; 「—」 for what a service has none of, as first words of all but LLMs. */
export function formatPercentiles(times: Percentiles | undefined): string {
  return times === undefined ? NONE : `${formatMs(times.p50)} / ${formatMs(times.p95)}`;
}

/** How much of an LLM's input hit the cache: 「72.3%」; 「—」 for other services, or where no call said. */
export function formatCacheHitRate(row: Pick<ServiceHealth, "cache_hit_rate">): string {
  return row.cache_hit_rate == null ? NONE : formatPercent(row.cache_hit_rate);
}

/** 「2 张剥掉了 tag：裸露 2、外貌 1」; null where none were, and for services other than image generation. */
export function strippedText(row: Pick<ServiceHealth, "stripped_photos" | "stripped">): string | null {
  const words = copy.admin.dashboard.health;
  if (!row.stripped_photos || !row.stripped) return null;
  const kinds = Object.entries(row.stripped)
    .map(([crossing, n]) => `${words.crossings[crossing as Crossing] ?? crossing} ${formatCount(n ?? 0)}`)
    .join("、");
  return words.stripped(formatCount(row.stripped_photos), kinds);
}

/** A health row named in a sentence: 「扮演角色的 LLM（自带密钥）」. */
export function healthRowName(row: ServiceHealth): string {
  return row.own_key ? `${serviceName(row)}（${copy.admin.dashboard.health.own}）` : serviceName(row);
}

export type TurnResult = "success" | "failed" | "rejected" | "left";

export const TURN_RESULTS: TurnResult[] = ["success", "failed", "rejected", "left"];

/** How a call turned out (GLOSSARY「接通」): a user hanging up or leaving is why it ended,
 * not how it turned out. */
export type CallResult = "success" | "failed" | "rejected" | "not_connected";

export const CALL_RESULTS: CallResult[] = ["success", "failed", "rejected", "not_connected"];

export type CallEndReason =
  | "hung_up"
  | "left"
  | "duration_limit"
  | "turn_limit"
  | "character_hung_up"
  | "banned"
  | "deleted"
  | "failed"
  | "shutdown";

export const CALL_END_REASONS: CallEndReason[] = [
  "hung_up",
  "left",
  "duration_limit",
  "turn_limit",
  "character_hung_up",
  "banned",
  "deleted",
  "failed",
  "shutdown",
];

/** Turns and calls in one stretch of the range, and how many users took any. */
export type TrendPoint = { start: string; turns: number; calls: number; active_users: number };

export type Bucket = "hour" | "6h" | "day";

export type Dashboard = {
  range: DashboardRange;
  from: string;
  to: string;
  /** How long each stretch of `trend` is. */
  bucket: Bucket;
  health: ServiceHealth[];
  /** Users who took any turn or call. */
  active_users: number;
  turns: {
    total: number;
    by_result: Record<TurnResult, number>;
    own_keys: number;
    /** Turns taken in calls, out of `total`. */
    in_calls: number;
    time_to_speak_ms: { count: number; p50: number | null; p90: number | null };
  };
  calls: {
    total: number;
    by_result: Record<CallResult, number>;
    own_keys: number;
    /** How long the calls that connected lasted. */
    duration_s: { p50: number | null; p90: number | null };
    /** How many calls lasted from `from_s` up to `to_s` (null: no end). */
    durations: { from_s: number; to_s: number | null; calls: number }[];
    end_reasons: Record<CallEndReason, number>;
  };
  trend: TrendPoint[];
  /** `name` is null for a character since removed from the configuration. */
  characters: { character_id: string; name: string | null; turns: number; calls: number }[];
  /** How the judge model judged the turns, where affinity is on (GLOSSARY「好感度」); null off. */
  affinity: AffinitySection | null;
  /** Her reach-outs (GLOSSARY「搭话」); null where they are off and none were made in the range. */
  reach_outs: ReachOutsSection | null;
  /** How many times each daily task was done (GLOSSARY「每日任务」); null where affinity is off and
   * none were done in the range. */
  daily_tasks: DailyTasksSection | null;
  /** The achievements' stages unlocked and what they gave (GLOSSARY「成就」); null where `daily_tasks` is. */
  achievements: AchievementsSection | null;
  /** What the deployer's keys cost, estimated at the prices (`lib/adminPrices`). */
  cost: Cost;
};

/** The dashboard's「搭话」section: how many times characters reached out, how many times that
 * failed, and the share of those reached out the user answered (null of none). */
export type ReachOutsSection = { succeeded: number; failed: number; answered_share: number | null };

/** The section's tiles, as the page shows them; none where it is empty. */
export function reachOutTiles(section: ReachOutsSection | null): { id: string; label: string; value: string }[] {
  if (section === null) return [];
  const words = copy.admin.dashboard.reachOuts.tiles;
  return [
    { id: "succeeded", label: words.succeeded, value: formatCount(section.succeeded) },
    { id: "failed", label: words.failed, value: formatCount(section.failed) },
    {
      id: "answered",
      label: words.answered,
      value: section.answered_share === null ? NONE : formatPercent(section.answered_share),
    },
  ];
}

/** The dashboard's「每日任务」section: how many times each daily task was done, by its id. */
export type DailyTasksSection = { one_turn: number; one_gift: number; ten_turns: number };

const DAILY_TASK_IDS = ["one_turn", "one_gift", "ten_turns"] as const;

/** The section's tiles, as the page shows them, in the order of the gifts page; none where it is empty. */
export function dailyTaskTiles(section: DailyTasksSection | null): { id: string; label: string; value: string }[] {
  if (section === null) return [];
  const words = copy.admin.dashboard.tasksAndAchievements.tiles;
  return DAILY_TASK_IDS.map((id) => ({ id, label: words[id], value: formatCount(section[id]) }));
}

/** The dashboard's achievements, beside the daily tasks: how many stages were unlocked, the draw
 * chances they gave, and the reset cards characters complete gave. */
export type AchievementsSection = { stages: number; draws: number; reset_cards: number };

const ACHIEVEMENT_TILE_IDS = ["stages", "draws", "reset_cards"] as const;

/** The achievements' tiles, as the page shows them after the daily tasks'; none where they are empty. */
export function achievementTiles(section: AchievementsSection | null): { id: string; label: string; value: string }[] {
  if (section === null) return [];
  const words = copy.admin.dashboard.tasksAndAchievements.tiles;
  return ACHIEVEMENT_TILE_IDS.map((id) => ({ id, label: words[id], value: formatCount(section[id]) }));
}

/** The levels the judge model judges a turn at, from hurting her (0) to making her very happy (4). */
export const AFFINITY_LEVELS = [0, 1, 2, 3, 4] as const;

/** One character's judged turns: only those recorded count (`judged`), and the shares of them
 * that raised and lowered her affinity are null for too few of them; the cooldowns they began,
 * and how often she hung up a call or ended a conversation, whatever for (`she_ended`).
 * `name` is null for a character since removed from the configuration. */
export type AffinityCharacter = {
  character_id: string;
  name: string | null;
  judged: number;
  raised_share: number | null;
  lowered_share: number | null;
  cooldowns: number;
  she_ended: number;
};

/** The dashboard's「好感度」section: the cooldowns begun and for how many users, the turns out
 * of line and manipulative, how many at each level, and each character, most judged first. */
export type AffinitySection = {
  cooldowns: number;
  cooled_users: number;
  out_of_line: number;
  manipulation: number;
  /** How many turns at each of `AFFINITY_LEVELS`. */
  levels: number[];
  characters: AffinityCharacter[];
};

/** A character's row of the section's table, as the page shows it: in the backend's order,
 * the shares a dash for too few; on a phone, the row ends on the shares (`end`), the rest
 * beneath (`details`). */
export type AffinityRow = {
  id: string;
  name: string | null;
  judged: string;
  raised: string;
  lowered: string;
  cooldowns: string;
  she_ended: string;
  end: string;
  details: string;
};

export function affinityRows(characters: AffinityCharacter[]): AffinityRow[] {
  const words = copy.admin.dashboard.affinity.characters;
  const share = (rate: number | null) => (rate === null ? NONE : formatPercent(rate));
  return characters.map((c) => {
    const [raised, lowered] = [share(c.raised_share), share(c.lowered_share)];
    const [judged, cooldowns, sheEnded] = [c.judged, c.cooldowns, c.she_ended].map(formatCount);
    return {
      id: c.character_id,
      name: c.name,
      judged,
      raised,
      lowered,
      cooldowns,
      she_ended: sheEnded,
      end: words.end(raised, lowered),
      details: words.details(judged, cooldowns, sheEnded),
    };
  });
}

/** How many of a list's rows the dashboard shows while it is folded. */
export const FOLDED_ROWS = 5;

/** How many of `count` rows are shown while folded: all of them when no more than one would be
 * left out (a button to show one more would take the place of the row it hides). */
export function foldedCount(count: number): number {
  return count <= FOLDED_ROWS + 1 ? count : FOLDED_ROWS;
}

export async function getDashboard(range: DashboardRange): Promise<Dashboard> {
  return json(await api(`/api/admin/dashboard?range=${range}`));
}

/** How often the page, while it can be seen, fetches the dashboard and the calls in progress
 * again. */
export const DASHBOARD_REFRESH_MS = 60_000;

/** When the numbers were fetched: "16:25" today, with the day before it on another day. */
export function formatFetchedAt(at: Date, now: Date = new Date()): string {
  const clock = `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
  const sameDay =
    at.getFullYear() === now.getFullYear() && at.getMonth() === now.getMonth() && at.getDate() === now.getDate();
  return sameDay ? clock : `${at.getMonth() + 1}月${at.getDate()}日 ${clock}`;
}

/** How many calls are in progress now, connected or still connecting. */
export async function getCallsInProgress(): Promise<number> {
  const live: { in_progress: number } = await json(
    await api("/api/admin/calls/live"),
  );
  return live.in_progress;
}

const NONE = "—";

/** A count, comma'd; from ten thousand on, in 万 to one decimal; from a hundred million on
 * (or what would round up to it), in 亿 to two. */
export function formatCount(n: number): string {
  const wan = Number((n / 10_000).toFixed(1));
  if (Math.abs(wan) >= 10_000) return `${(n / 100_000_000).toFixed(2).replace(/\.?0+$/, "")}亿`;
  if (Math.abs(n) >= 10_000) return `${trim(n / 10_000)}万`;
  return Math.round(n).toLocaleString("en-US");
}

/** `part` of `whole` as a percentage, to one decimal at most; a sliver is never shown as 0. */
export function formatShare(part: number, whole: number): string {
  if (whole <= 0) return NONE;
  return formatPercent(part / whole);
}

export function formatPercent(rate: number): string {
  if (rate <= 0) return "0%";
  if (rate < 0.001) return "<0.1%";
  return `${trim(rate * 100)}%`;
}

/** A duration in milliseconds: under a second in ms, else in seconds to one decimal. */
export function formatMs(ms: number | null): string {
  if (ms === null) return NONE;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${trim(ms / 1000)} s`;
}

/** How long a call lasted: "45 秒", "2 分 5 秒", "1 小时 2 分". */
export function formatDuration(seconds: number | null): string {
  if (seconds === null) return NONE;
  const whole = Math.floor(seconds);
  const [hours, minutes, secs] = [Math.floor(whole / 3600), Math.floor((whole % 3600) / 60), whole % 60];
  if (hours > 0) return minutes > 0 ? `${hours} 小时 ${minutes} 分` : `${hours} 小时`;
  if (minutes > 0) return secs > 0 ? `${minutes} 分 ${secs} 秒` : `${minutes} 分`;
  return `${secs} 秒`;
}

/** A stretch of the call duration distribution, from `from` up to `to` seconds (null: no
 * end), in whole minutes. */
export function durationRange(from: number, to: number | null): string {
  if (to === null) return `${from / 60} 分钟以上`;
  if (from === 0) return `${to / 60} 分钟以内`;
  return `${from / 60}–${to / 60} 分钟`;
}

function trim(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}

/** How a service is doing by its error rate; `idle` with no calls to tell by. */
export type HealthStatus = "ok" | "degraded" | "failing" | "idle";

export function healthStatus(row: Pick<ServiceHealth, "calls" | "error_rate">): HealthStatus {
  if (row.calls === 0) return "idle";
  if (row.error_rate < 0.02) return "ok";
  if (row.error_rate < 0.1) return "degraded";
  return "failing";
}

/** What a service's calls were billed for, e.g. "输入 1.2万 · 输出 3,400 tokens"; how
 * many calls did not say, if any. */
export function formatUsage(usage: Usage[], unknown: number): string {
  const parts = usage.map(({ unit, input, output }) => formatAmount(unit, input, output));
  if (unknown > 0) parts.push(copy.admin.usage.unknown(formatCount(unknown)));
  return parts.join("；") || NONE;
}

/** 计费单位的叫法：tokens、字节、秒；后端的单位名原样传来，这里统一译出。 */
export function unitWord(unit: string): string {
  return copy.admin.usage.units[unit] ?? unit;
}

/** 一种单位的用量："1.2万 字节"，分输入输出时 "输入 1.2万 · 输出 3,400 tokens"。看板健康表和花费表共用。 */
export function formatAmount(unit: string, input: number | null, output: number | null): string {
  const word = unitWord(unit);
  const shown = input === null ? NONE : formatCount(input);
  if (output === null) return copy.admin.usage.amount(shown, word);
  return copy.admin.usage.inOut(shown, formatCount(output), word);
}

/** Where a stretch of the trend begins, as its axis labels it, in the browser's time. */
export function stretchLabel(start: string, bucket: Bucket): string {
  const at = new Date(start);
  const hour = `${String(at.getHours()).padStart(2, "0")}:00`;
  const day = `${at.getMonth() + 1}月${at.getDate()}日`;
  if (bucket === "day") return day;
  if (bucket === "6h") return at.getHours() === 0 ? day : hour;
  return hour;
}

/** The whole stretch, for its tooltip: "10月7日 14:00–15:00", or the day. */
export function stretchSpan(start: string, bucket: Bucket): string {
  const at = new Date(start);
  const day = `${at.getMonth() + 1}月${at.getDate()}日`;
  if (bucket === "day") return day;
  const hours = bucket === "6h" ? 6 : 1;
  const end = (at.getHours() + hours) % 24;
  const pad = (h: number) => `${String(h).padStart(2, "0")}:00`;
  return `${day} ${pad(at.getHours())}–${pad(end)}`;
}

/** Clean ticks for a count axis from 0 to at least `max`: 1, 2 or 5 times a power of ten
 * apart, at most `count` steps. */
export function countTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const rough = max / count;
  const power = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * power).find((s) => s >= rough) ?? 10 * power;
  const whole = Math.max(1, Math.round(step)); // counts tick on whole numbers
  const ticks = [];
  for (let tick = 0; tick < max + whole; tick += whole) ticks.push(tick);
  return ticks;
}

/**
 * The axis labels of the stretches `labelled` picks (index → label). Hourly and daily
 * stretches label themselves (`stretchLabel`). Six-hour stretches are labelled by their day
 * wherever the day changes since the label before, and by their hour otherwise: they line up
 * with UTC, so in most time zones none starts at local midnight, and labelling every fourth
 * one by its hour would print the same hour every day（东八区 7 天视图全是「12:00」）.
 */
export function axisLabels(starts: string[], bucket: Bucket, labelled: Set<number>): Map<number, string> {
  const labels = new Map<number, string>();
  let lastDay: string | null = null;
  for (const i of [...labelled].sort((a, b) => a - b)) {
    if (bucket !== "6h") {
      labels.set(i, stretchLabel(starts[i], bucket));
      continue;
    }
    const at = new Date(starts[i]);
    const day = `${at.getMonth() + 1}月${at.getDate()}日`;
    labels.set(i, day === lastDay ? `${String(at.getHours()).padStart(2, "0")}:00` : day);
    lastDay = day;
  }
  return labels;
}

/** Which of `n` stretches the axis labels, so that at most `most` labels fit: every
 * stretch, or every second, third… counting back from the latest. */
export function labelledStretches(n: number, most: number): Set<number> {
  const every = Math.max(1, Math.ceil(n / most));
  const labelled = new Set<number>();
  for (let i = n - 1; i >= 0; i -= every) labelled.add(i);
  return labelled;
}

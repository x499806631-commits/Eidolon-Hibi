// Stats (GLOSSARY.md「统计」): the user's own dealings with the characters, as `/api/stats`
// sums them up, and what the page makes of them: the heatmap's weeks and shades, the part
// of the day the portrait speaks of, who is ranked, which tiers are shown, and which blocks
// are. The backend gives only numbers; how many of what are shown, and how, is decided here.

import { copy } from "@/i18n";
import type { Tier } from "@/lib/affinity";

/** What `/api/stats` answers. Dates are the producer's (`YYYY-MM-DD`, their profile's time zone).
 * `days_known`: the days since the user signed up, that day the first.
 * `visits`: the days in a row they came (took a turn or gave a gift), now and at the longest.
 * `all_hours`: how many turns and gifts at each hour of the day, 0–23, all told.
 * `heatmap`: how many on each day from `from`, a Monday 52 weeks before this week's, to today.
 * `affinity`: each tier, from cold to intimate, and the characters in it; null while affinity is off.
 * `achievements`: the stages unlocked in all and the characters complete, whatever the range; null while affinity is off.
 * `range`: what changes with the range, `span`: see `InRange`. */
export type Stats = {
  days_known: number;
  visits: { current: number; longest: number };
  all_hours: number[];
  heatmap: { from: string; counts: number[] };
  affinity: TierGroup[] | null;
  achievements: { stages: number; complete: number } | null;
  range: InRange;
};

/** The characters in a tier, in the idols' order: by tier alone, so that none reads higher than another. */
export type TierGroup = { tier: Tier; characters: string[] };

/** The ranges the lower part can be over: the past 30 days or 365, today the last, or all of it. */
export const STATS_RANGES = ["30d", "1y", "all"] as const;

export type StatsRange = (typeof STATS_RANGES)[number];

/** A character's turns in the range and how long her calls in it lasted once connected, in
 * seconds, and the days since the first thing still left with her (a message, an event or a
 * call, that day the first), whatever the range. */
export type CharacterInRange = { character_id: string; turns: number; days_known: number; call_seconds: number };

/** What changes with the range (`span`).
 * `turns`: the turns taken in it; `by_hour`: how many turns and gifts at each hour, 0–23.
 * `characters`: each she has anything left with, in the idols' order.
 * `calls`: how many connected in it, and how long they lasted all told once connected, in
 * seconds (ADR-0021: only those since calls were kept).
 * `gifts`: how many given, how many she liked as they were given, and whom the most went to
 * (the first in the idols' order of those tied; null when none were); null while affinity,
 * and with it gifts, is off. */
export type InRange = {
  span: StatsRange;
  turns: number;
  by_hour: number[];
  characters: CharacterInRange[];
  calls: { count: number; seconds: number };
  gifts: { given: number; liked: number; most: { character_id: string; given: number } | null } | null;
};

/** How many a ranking (「聊得最多」「通话最久」) has at most. */
export const RANKING_SIZE = 5;

/** A character ranked, with her bar's length as a share of the first's, 0–1. */
export type Ranked = CharacterInRange & { share: number };

/** The characters with the most of `by` in the range, at most five, those with none left out;
 * tied, in the order given (the idols'). */
function rank(characters: CharacterInRange[], by: (c: CharacterInRange) => number): Ranked[] {
  const ranked = characters
    .filter((c) => by(c) > 0)
    .sort((a, b) => by(b) - by(a))
    .slice(0, RANKING_SIZE);
  const most = ranked[0] ? by(ranked[0]) : 0;
  return ranked.map((c) => ({ ...c, share: by(c) / most }));
}

/** The characters talked with most in the range, by their turns. */
export function mostTalked(characters: CharacterInRange[]): Ranked[] {
  return rank(characters, (c) => c.turns);
}

/** The characters on the phone with longest in the range, by how long her calls lasted. */
export function mostCalled(characters: CharacterInRange[]): Ranked[] {
  return rank(characters, (c) => c.call_seconds);
}

/** How long calls lasted, `seconds` of them, in hours and minutes: to the nearest minute, but
 * a minute at least, however short, so that a call ranked never reads 0. */
export function callTime(seconds: number): { hours: number; minutes: number } {
  const minutes = seconds > 0 ? Math.max(1, Math.round(seconds / 60)) : 0;
  return { hours: Math.floor(minutes / 60), minutes: minutes % 60 };
}

/** How long calls lasted, as a ranking's row reads it: 「2 小时 5 分」, or 「12 分」 under an hour. */
export function callTimeText(seconds: number): string {
  const t = copy.settings.stats;
  const { hours, minutes } = callTime(seconds);
  return hours > 0 ? t.calledHours(hours, minutes) : t.calledMinutes(minutes);
}

/** The four parts of the day the portrait speaks of, each six hours from midnight. */
export const DAY_PARTS = ["night", "morning", "afternoon", "evening"] as const;

export type DayPart = (typeof DAY_PARTS)[number];

/** The parts of the day the user came at most, in the order of the day (more than one when
 * tied); none if they never came. */
export function busiestParts(hours: number[]): DayPart[] {
  const counts = DAY_PARTS.map((_, i) => hours.slice(i * 6, i * 6 + 6).reduce((sum, n) => sum + n, 0));
  const most = Math.max(...counts);
  return most === 0 ? [] : DAY_PARTS.filter((_, i) => counts[i] === most);
}

/** One line on when the user comes, by the part of the day they came at most: both of two
 * tied, and that they come at any time when three or four are; null if they never came. */
export function portrait(hours: number[]): string | null {
  const t = copy.settings.stats.portrait;
  const parts = busiestParts(hours).map((part) => t.parts[part]);
  if (parts.length === 0) return null;
  if (parts.length > 2) return t.anyTime;
  return t.comesAt(parts.join(t.and));
}

export type HeatLevel = 0 | 1 | 2 | 3 | 4;

/** How dark a day's square is: 0 when they did not come, else 1–4 by its share of the
 * busiest day's count. */
export function heatLevel(count: number, most: number): HeatLevel {
  if (count <= 0 || most <= 0) return 0;
  return Math.min(4, Math.max(1, Math.ceil((count / most) * 4))) as HeatLevel;
}

/** A day of the heatmap: its date (`YYYY-MM-DD`), its month and day of the month, how many
 * turns and gifts, and its shade. */
export type HeatCell = { date: string; month: number; day: number; count: number; level: HeatLevel };

/** The heatmap's columns, a week each from Monday at the top; the last only up to today. */
export function heatmapWeeks(heatmap: Stats["heatmap"]): HeatCell[][] {
  const most = Math.max(0, ...heatmap.counts);
  const start = Date.parse(`${heatmap.from}T00:00:00Z`);
  const weeks: HeatCell[][] = [];
  heatmap.counts.forEach((count, i) => {
    if (i % 7 === 0) weeks.push([]);
    const date = new Date(start + i * 86_400_000).toISOString().slice(0, 10);
    const [month, day] = [Number(date.slice(5, 7)), Number(date.slice(8, 10))];
    weeks[weeks.length - 1].push({ date, month, day, count, level: heatLevel(count, most) });
  });
  return weeks;
}

/** The months to label above the heatmap, by the column the first of each falls in; one
 * closer than `apart` columns to the label before it is left out. */
export function heatmapMonths(weeks: HeatCell[][], apart: number): Map<number, string> {
  const months = new Map<number, string>();
  let last = -Infinity;
  weeks.forEach((week, column) => {
    const first = week.find((cell) => cell.day === 1);
    if (!first || column - last < apart) return;
    months.set(column, copy.settings.stats.month(first.month));
    last = column;
  });
  return months;
}

/** The tiers the affinity groups show, those with anyone in them, from intimate down to cold; none while affinity is off. */
export function affinityGroups(affinity: TierGroup[] | null): TierGroup[] {
  return (affinity ?? []).filter((group) => group.characters.length > 0).reverse();
}

/** How many of a tier the affinity groups show folded: the first five, in the idols' order. */
export const TIER_PREVIEW = 5;

/** How many of the `count` in a tier are shown while it is folded: all of them when no more than
 * one would be left out (a 「+1」 would take the place of the one it hides). */
export function tierPreview(count: number): number {
  return count <= TIER_PREVIEW + 1 ? count : TIER_PREVIEW;
}

export type StatsSections = {
  isEmpty: boolean;
  visits: boolean;
  portrait: boolean;
  heatmap: boolean;
  affinity: boolean;
  achievements: boolean;
  totals: boolean;
  /** 总数里的「送出的礼物」. */
  giftsGiven: boolean;
  mostTalked: boolean;
  mostCalled: boolean;
  hours: boolean;
  gifts: boolean;
};

/** Which blocks of the page are shown: none, but a line and a way to the conversations, when
 * nothing is left with any of them, no turn, gift or call (`isEmpty`; the characters are
 * those anything is left with, whatever the range); otherwise each that has something, and
 * the totals and the achievements, 0 or not, whatever the range. Calls alone are not coming
 * (`visits`). While affinity and gifts are off, none of them: no affinity groups, achievements,
 * gifts, or gifts given among the totals. */
export function statsSections(stats: Stats): StatsSections {
  const hasCome = stats.all_hours.some((n) => n > 0);
  const { range } = stats;
  const isEmpty = !hasCome && range.characters.length === 0;
  return {
    isEmpty,
    visits: hasCome && stats.visits.longest > 0,
    portrait: hasCome,
    heatmap: stats.heatmap.counts.some((n) => n > 0),
    affinity: !isEmpty && affinityGroups(stats.affinity).length > 0,
    achievements: !isEmpty && stats.achievements !== null,
    totals: !isEmpty,
    giftsGiven: !isEmpty && range.gifts !== null,
    mostTalked: range.characters.some((c) => c.turns > 0),
    mostCalled: range.characters.some((c) => c.call_seconds > 0),
    hours: range.by_hour.some((n) => n > 0),
    gifts: (range.gifts?.given ?? 0) > 0,
  };
}

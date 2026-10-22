// 日历（日历页 `/calendar`）：偶像的生日、节日和制作人自己的生日，按月排成格子和一张清单。
// 偶像的生日和节日由后端按年份给（`/api/calendar?year=`，农历的节日已经换成那一年的公历日期），
// 制作人的生日在档案里。哪天是今天按制作人的时区算，和对话里她们知道的「今天」是同一天。

import { api, json, type Birthday } from "@/lib/api";

/** 一年中的某一天：几月几日，不带年份。 */
export type MonthDay = { month: number; day: number };

/** 一个节日：名字、一句背景和小图标（emoji；都可能是空的），第一天；持续几天的（樱花季）还有最后一天，不跨年。 */
export type CalendarHoliday = { name: string; note: string; icon: string; start: MonthDay; end: MonthDay | null };

/** 一份纪念日：和某位偶像认识满一百天或满整年的日子，连同那是什么（「认识第 100 天」）。 */
export type CalendarAnniversary = { character_id: string; month: number; day: number; label: string };

/** 一个约定（GLOSSARY.md「约定」）：答应在某天为她做的一件事，还没做到。 */
export type CalendarPromise = { id: number; character_id: string; month: number; day: number; text: string };

/** 日历接口给的一年：每位偶像的生日、这一年有的节日、这个用户自己的纪念日，和他还没做到的约定。 */
export type CalendarYear = {
  birthdays: { character_id: string; month: number; day: number }[];
  holidays: CalendarHoliday[];
  anniversaries: CalendarAnniversary[];
  promises: CalendarPromise[];
};

/** 某一天的一件事：一位偶像的生日、制作人的生日、一个节日、和某位偶像的纪念日，或一个约定。 */
export type DayEvent =
  | { kind: "birthday"; characterId: string }
  | { kind: "producer" }
  | { kind: "holiday"; holiday: CalendarHoliday }
  | { kind: "anniversary"; characterId: string; label: string }
  | { kind: "promise"; characterId: string; promiseId: number; text: string };

/** 这个月里有事的一天，和那天的事（偶像的生日、制作人的生日、节日，依次）。 */
export type MarkedDay = { day: number; events: DayEvent[] };

/** 读某一年的偶像生日和节日。 */
export async function getCalendar(year: number): Promise<CalendarYear> {
  return json(await api(`/api/calendar?${new URLSearchParams({ year: String(year) })}`));
}

/** 说这个约定已经做到了：它也就像送到了礼物一样消失。 */
export async function fulfilPromise(id: number): Promise<void> {
  await json(await api(`/api/promises/${id}/fulfil`, { method: "POST" }));
}

/** 某个时区现在是几年几月几日。 */
export function todayIn(timeZone: string, now: Date): { year: number; month: number; day: number } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(now);
  const part = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { year: part("year"), month: part("month"), day: part("day") };
}

function isLeap(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

/** 这个月有几天。 */
export function daysInMonth(year: number, month: number): number {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

/** 上个月或下个月（`step` 为 -1 或 1），跨年时年份跟着变。 */
export function shiftMonth(year: number, month: number, step: number): { year: number; month: number } {
  const index = year * 12 + (month - 1) + step;
  return { year: Math.floor(index / 12), month: (index % 12) + 1 };
}

/**
 * 月历的格子：一周一行，星期一在最左边；不属于这个月的格子是 null，最后一行补齐到 7 格。
 */
export function monthWeeks(year: number, month: number): (number | null)[][] {
  const first = new Date(Date.UTC(year, month - 1, 1)).getUTCDay(); // 0 是星期日
  const cells: (number | null)[] = Array.from({ length: (first + 6) % 7 }, () => null);
  for (let day = 1; day <= daysInMonth(year, month); day++) cells.push(day);
  while (cells.length % 7 !== 0) cells.push(null);
  return Array.from({ length: cells.length / 7 }, (_, i) => cells.slice(i * 7, i * 7 + 7));
}

/** 这一年里生日落在哪天：2 月 29 日的生日在平年落在 2 月 28 日，和后端一样。 */
function birthdayIn(year: number, birthday: MonthDay): MonthDay {
  return birthday.month === 2 && birthday.day === 29 && !isLeap(year) ? { month: 2, day: 28 } : birthday;
}

/**
 * 这个月里有事的日子，按日期排。持续几天的节日只记在它在这个月的第一天（樱花季 4 月里记在 4 月 1 日），
 * 格子和清单都不会每天重复一遍。
 */
export function monthEvents(
  year: number,
  month: number,
  calendar: CalendarYear,
  producerBirthday: Birthday | null,
): MarkedDay[] {
  const days = new Map<number, DayEvent[]>();
  const add = (day: number, event: DayEvent) => days.set(day, [...(days.get(day) ?? []), event]);
  for (const b of calendar.birthdays) {
    const on = birthdayIn(year, b);
    if (on.month === month) add(on.day, { kind: "birthday", characterId: b.character_id });
  }
  // 纪念日挨着她的生日一起排：都是和某位偶像有关的事，偶像的在前、制作人的生日和节日在后。
  for (const a of calendar.anniversaries) {
    if (a.month === month) add(a.day, { kind: "anniversary", characterId: a.character_id, label: a.label });
  }
  // 约定（答应在某天为她做的事）也排在这天，和偶像的事一起。
  for (const p of calendar.promises) {
    if (p.month === month) add(p.day, { kind: "promise", characterId: p.character_id, promiseId: p.id, text: p.text });
  }
  if (producerBirthday) {
    const on = birthdayIn(year, producerBirthday);
    if (on.month === month) add(on.day, { kind: "producer" });
  }
  for (const holiday of calendar.holidays) {
    const end = holiday.end ?? holiday.start;
    if (month < holiday.start.month || month > end.month) continue;
    add(month === holiday.start.month ? holiday.start.day : 1, { kind: "holiday", holiday });
  }
  return [...days.entries()].sort(([a], [b]) => a - b).map(([day, events]) => ({ day, events }));
}

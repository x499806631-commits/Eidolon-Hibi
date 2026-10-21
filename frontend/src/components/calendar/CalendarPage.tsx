"use client";

import { useEffect, useMemo, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { SectionHeading } from "@/components/app/SubpageHeader";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { useProducerTimezone } from "@/components/conversations/useProducerTimezone";
import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import { getProfile, type Birthday, type Character } from "@/lib/api";
import {
  getCalendar,
  monthEvents,
  monthWeeks,
  shiftMonth,
  todayIn,
  type CalendarYear,
  type DayEvent,
  type MarkedDay,
} from "@/lib/calendar";
import { toFailure, type Failure } from "@/lib/failure";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { BackIcon, CakeIcon, CalendarIcon, ForwardIcon } from "@/theme/icons";

const t = copy.calendar;

/** 一格里最多放几位偶像的头像，多的写成「+n」。 */
const MAX_FACES = 2;

type Characters = Map<string, Character>;

/**
 * 日历页（`/calendar`）：导航外壳里一栏，没有第二栏，版式同礼物页（内容最宽 `max-w-3xl`、居中）。
 * 上面是月历：星期一在最左边，周六的日期蓝、周日红；今天的格子一圈前景色描边、日期铺前景色的圆底。
 * 有生日的格子铺她的壁纸色（同一天几位时取第一位的），里面是她们的头像（最多两个，多的写「+n」）；
 * 制作人自己的生日是一个蛋糕，节日是它的小图标，电脑上下面再写名字。下面「这个月」把这些按日期列成清单，
 * 偶像的生日那行可以直接去找她。哪天是今天按制作人的时区算。
 */
export function CalendarPage() {
  const timeZone = useProducerTimezone();
  const today = useMemo(() => todayIn(timeZone, new Date()), [timeZone]);
  // 翻到的月份；还没翻过时跟着今天走（时区从档案读到以后今天可能换一天）。
  const [shown, setShown] = useState<{ year: number; month: number } | null>(null);
  const { year, month } = shown ?? today;
  const [years, setYears] = useState<ReadonlyMap<number, CalendarYear>>(new Map());
  const [birthday, setBirthday] = useState<Birthday | null | undefined>(undefined);
  const [failure, setFailure] = useState<Failure | null>(null);
  const { characters, go } = useDirectory();
  const byId: Characters = useMemo(() => new Map((characters ?? []).map((c) => [c.id, c])), [characters]);

  useEffect(() => {
    let current = true;
    // 读不到档案时当作没填生日，日历照样能看。
    getProfile().then(
      (p) => current && setBirthday(p.birthday),
      () => current && setBirthday(null),
    );
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => {
    if (years.has(year)) return;
    let current = true;
    getCalendar(year).then(
      (c) => current && setYears((m) => new Map(m).set(year, c)),
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [year, years]);

  const calendar = years.get(year);
  const marked = useMemo(
    () => (calendar && birthday !== undefined ? monthEvents(year, month, calendar, birthday) : null),
    [calendar, birthday, year, month],
  );
  const isThisMonth = year === today.year && month === today.month;
  const todayHere = isThisMonth ? today.day : null;

  function turn(step: number) {
    setFailure(null);
    setShown(shiftMonth(year, month, step));
  }

  return (
    <PageShell width="full" isInShell header={<h1 className="text-list-title">{t.title}</h1>}>
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
        {failure && <FailureNotice failure={failure} />}
        <div className="flex items-center gap-1">
          <h2 className="flex-1 text-header tabular-nums">{t.month(year, month)}</h2>
          {!isThisMonth && (
            <Button variant="outline" size="sm" onClick={() => setShown(null)}>
              {t.today}
            </Button>
          )}
          {/* 手机上触控目标不小于 44px */}
          <Button variant="ghost" size="icon-lg" className="max-md:size-11" aria-label={t.previous} onClick={() => turn(-1)}>
            <BackIcon />
          </Button>
          <Button variant="ghost" size="icon-lg" className="max-md:size-11" aria-label={t.next} onClick={() => turn(1)}>
            <ForwardIcon />
          </Button>
        </div>
        {marked === null || characters === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : (
          <>
            <MonthGrid year={year} month={month} marked={marked} today={todayHere} characters={byId} />
            <SectionHeading>{t.thisMonth}</SectionHeading>
            {marked.length === 0 ? (
              <p className="text-body text-muted-foreground">{t.empty}</p>
            ) : (
              <ul className="flex flex-col">
                {marked.flatMap(({ day, events }) =>
                  events.map((event, i) => (
                    <EventRow
                      key={`${day}-${i}`}
                      year={year}
                      month={month}
                      day={day}
                      event={event}
                      isToday={day === todayHere}
                      characters={byId}
                      onGo={(id) => go(`/conversation/${encodeURIComponent(id)}`)}
                    />
                  )),
                )}
              </ul>
            )}
            {birthday === null && <p className="text-subtitle text-muted-foreground">{t.addBirthday}</p>}
          </>
        )}
      </div>
    </PageShell>
  );
}

/** 一件事在清单和读屏里叫什么；偶像已经不在角色配置里时没有名字（不显示）。 */
function eventTitle(event: DayEvent, characters: Characters): string | null {
  if (event.kind === "birthday") {
    const c = characters.get(event.characterId);
    return c ? t.birthdayOf(c.name) : null;
  }
  return event.kind === "producer" ? t.producerBirthday : event.holiday.name;
}

/** 星期几的那个字（一 … 日）。 */
function weekdayOf(year: number, month: number, day: number): string {
  return t.weekdays[(new Date(Date.UTC(year, month - 1, day)).getUTCDay() + 6) % 7];
}

/** 月历：表头一行星期，下面一周一行。 */
function MonthGrid({
  year,
  month,
  marked,
  today,
  characters,
}: {
  year: number;
  month: number;
  marked: MarkedDay[];
  today: number | null;
  characters: Characters;
}) {
  const events = new Map(marked.map((m) => [m.day, m.events]));
  return (
    <section aria-label={t.month(year, month)} className="flex flex-col gap-1">
      <div aria-hidden className="grid grid-cols-7 text-center text-meta text-muted-foreground">
        {t.weekdays.map((w, i) => (
          <span key={w} className={weekendText(i)}>
            {w}
          </span>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {monthWeeks(year, month)
          .flat()
          .map((day, i) =>
            day === null ? (
              <div key={`blank-${i}`} />
            ) : (
              <DayCell
                key={day}
                weekday={i % 7}
                month={month}
                day={day}
                events={events.get(day) ?? []}
                isToday={day === today}
                characters={characters}
              />
            ),
          )}
      </div>
    </section>
  );
}

/** 周六、周日（星期一起算的第 6、7 列）的字色；平日是 undefined，沿用外面的。 */
function weekendText(weekday: number): string | undefined {
  if (weekday === 5) return "text-saturday";
  if (weekday === 6) return "text-sunday";
  return undefined;
}

/** 月历的一格：日期，下面这天的生日头像、蛋糕和节日。读屏读一整句（`dayLabel`）。 */
function DayCell({
  weekday,
  month,
  day,
  events,
  isToday,
  characters,
}: {
  /** 星期几，星期一是 0。 */
  weekday: number;
  month: number;
  day: number;
  events: DayEvent[];
  isToday: boolean;
  characters: Characters;
}) {
  const faces = events.flatMap((e) => {
    const c = e.kind === "birthday" ? characters.get(e.characterId) : undefined;
    return c ? [c] : [];
  });
  const isProducers = events.some((e) => e.kind === "producer");
  const holidays = events.flatMap((e) => (e.kind === "holiday" ? [e.holiday] : []));
  const titles = events.flatMap((e) => eventTitle(e, characters) ?? []);
  const her = faces[0];
  return (
    <div
      style={her ? herVars(her.color) : undefined}
      className={cn(
        "flex min-h-16 min-w-0 flex-col items-center gap-1 rounded-xl p-1 md:min-h-24",
        her ? "bg-her-wall" : titles.length > 0 && "bg-muted",
        isToday && "ring-2 ring-foreground ring-inset",
      )}
    >
      <span className="sr-only">
        {t.dayLabel(t.day(month, day), [...(isToday ? [t.today] : []), ...titles].join("，"))}
      </span>
      <span
        aria-hidden
        className={cn(
          "flex size-6 items-center justify-center rounded-full text-sm tabular-nums",
          isToday ? "bg-foreground font-medium text-background" : !her && weekendText(weekday),
        )}
      >
        {day}
      </span>
      <div aria-hidden className="flex flex-wrap items-center justify-center gap-0.5">
        {faces.slice(0, MAX_FACES).map((c) => (
          <MarkedAvatar key={c.id} name={c.name} src={c.avatar_url} size="cell" />
        ))}
        {faces.length > MAX_FACES && <span className="text-meta text-muted-foreground">{t.more(faces.length - MAX_FACES)}</span>}
        {isProducers && <CakeIcon className="size-4 text-foreground md:size-5" />}
        {holidays.map((h) =>
          h.icon ? (
            <span key={h.name} className="text-sm leading-none md:text-base">
              {h.icon}
            </span>
          ) : (
            <span key={h.name} className="size-1.5 rounded-full bg-muted-foreground md:hidden" />
          ),
        )}
      </div>
      {holidays.map((h) => (
        <span key={h.name} aria-hidden className="w-full truncate text-center text-meta text-muted-foreground max-md:hidden">
          {h.name}
        </span>
      ))}
    </div>
  );
}

/** 清单的一行：日期、头像（偶像的生日）或图标、这件事和节日的说明；偶像的生日右边「去找她」。 */
function EventRow({
  year,
  month,
  day,
  event,
  isToday,
  characters,
  onGo,
}: {
  year: number;
  month: number;
  day: number;
  event: DayEvent;
  isToday: boolean;
  characters: Characters;
  onGo: (characterId: string) => void;
}) {
  const title = eventTitle(event, characters);
  if (title === null) return null;
  const character = event.kind === "birthday" ? characters.get(event.characterId) : undefined;
  const holiday = event.kind === "holiday" ? event.holiday : null;
  const lasting =
    holiday?.end &&
    t.lasting(t.day(holiday.start.month, holiday.start.day), t.day(holiday.end.month, holiday.end.day));
  const note = [lasting, holiday?.note].filter(Boolean).join("。");
  return (
    <li className="flex items-center gap-3 border-b py-2.5 last:border-b-0">
      <span
        className={cn(
          "w-20 shrink-0 text-meta tabular-nums",
          isToday ? "font-medium text-foreground" : "text-muted-foreground",
        )}
      >
        {isToday ? t.today : t.date(month, day, weekdayOf(year, month, day))}
      </span>
      {character ? (
        <MarkedAvatar name={character.name} src={character.avatar_url} size="sm" />
      ) : (
        <span aria-hidden className="flex size-6 shrink-0 items-center justify-center rounded-full bg-muted">
          {event.kind === "producer" ? (
            <CakeIcon className="size-3.5" />
          ) : holiday?.icon ? (
            <span className="text-sm leading-none">{holiday.icon}</span>
          ) : (
            <CalendarIcon className="size-3.5" />
          )}
        </span>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-body">{title}</span>
        {note && <span className="text-subtitle text-muted-foreground">{note}</span>}
      </div>
      {character && (
        <Button variant="outline" size="sm" onClick={() => onGo(character.id)}>
          {t.goChat}
        </Button>
      )}
    </li>
  );
}

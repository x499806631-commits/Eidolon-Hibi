"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory, useWide } from "@/components/conversations/ConversationDirectory";
import { useProducerTimezone } from "@/components/conversations/useProducerTimezone";
import { TicketDialog } from "@/components/gifts/TicketDialog";
import { sceneSize } from "@/components/home/RoomScene";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import {
  byPlace,
  DATE_SPOTS,
  fanned,
  getCityMap,
  MAP_HEIGHT,
  MAP_WIDTH,
  PLACE_AT,
  type CityMap,
  type DateOnMap,
  type DateSpot,
  type IdolOnMap,
  type Place,
} from "@/lib/cityMap";
import { toFailure, type Failure } from "@/lib/failure";
import { getGiftState } from "@/lib/gifts";
import { encounter, ENCOUNTER_PLACES } from "@/lib/outing";
import { timeOfDay } from "@/lib/home";
import { scheduleText } from "@/lib/schedule";
import { cn } from "@/lib/utils";
import { BackHomeIcon } from "@/theme/icons";

import { MapArt } from "./MapArt";

const t = copy.map;
/** 多久重新看一次她们在哪（毫秒）：行程一段一段地过。 */
const REFRESH_MS = 5 * 60_000;
/** 画布上头像的直径（像素，按画布坐标，跟着地图缩放）。 */
const AVATAR = 76;

type Mode = "now" | "dates";

/**
 * 地图（`/home/map`，GLOSSARY.md「地图」）：页首「回家」、「地图」，右边「现在 / 约会」的切换。下面整块是一张画出来的东京，
 * 同家里的屋子一样按高度铺满、手机上左右划着看。
 * 「现在」：每位今天有行程的偶像，头像在她此刻所在的地方（同一处的排成一排，立在那栋建筑上面）；点头像，底下一张小卡说她在做什么，
 * 能直接去找她。行程关着时地图上没有人，说一句为什么。
 * 「约会」：能约的地方醒目起来，约好的约会在那一处旁边挂个小牌子；点一处，用邀约券约她去（同礼物页的邀约券，
 * 一起做什么先填好），手上的邀约券有几张写在页首。
 */
export function MapPage() {
  const { characters, go } = useDirectory();
  const isWide = useWide();
  const timeZone = useProducerTimezone();
  const [map, setMap] = useState<CityMap | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [mode, setMode] = useState<Mode>("now");
  const [picked, setPicked] = useState<string | null>(null);
  const [spot, setSpot] = useState<DateSpot | null>(null);
  const [dateTickets, setDateTickets] = useState<number | null>(null);
  const [now, setNow] = useState(() => new Date());
  const scroller = useRef<HTMLDivElement>(null);
  const [space, setSpace] = useState({ width: 0, height: 0 });

  const load = () => {
    getCityMap().then(setMap, (e) => setFailure(toFailure(e)));
    getGiftState().then(
      (state) => setDateTickets(state.tickets.find((held) => held.kind === "date")?.count ?? 0),
      () => setDateTickets(null), // 礼物关着：不说有几张
    );
  };

  useEffect(() => {
    load();
    const timer = setInterval(() => {
      setNow(new Date());
      getCityMap().then(setMap, () => {});
    }, REFRESH_MS);
    return () => clearInterval(timer);
  }, []);

  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => setSpace({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [map === null]);
  const size = sceneSize(space.width, space.height, isWide, MAP_WIDTH / MAP_HEIGHT);
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
  }, [size.width]);

  const people = useMemo(() => new Map((characters ?? []).map((c) => [c.id, c])), [characters]);
  const idols = (map?.idols ?? []).filter((idol) => people.has(idol.character_id));
  const chosen = idols.find((idol) => idol.character_id === picked) ?? null;
  const scale = size.width / MAP_WIDTH;

  const hint =
    mode === "dates"
      ? dateTickets === 0
        ? t.noTickets
        : t.hintDates
      : map && !map.schedules
        ? t.schedulesOff
        : map && idols.length === 0
          ? t.nobody
          : isWide
            ? t.hintNow
            : t.hintSwipe;

  return (
    <PageShell
      width="full"
      isInShell
      isScrollable={false}
      header={
        <div className="flex w-full items-center gap-2">
          <Button variant="ghost" size="sm" onClick={() => go("/home")} aria-label={t.back}>
            <BackHomeIcon />
            <span className="max-sm:hidden">{t.back}</span>
          </Button>
          <h1 className="flex-1 text-list-title">{t.title}</h1>
          {mode === "dates" && dateTickets !== null && (
            <span className="text-meta text-muted-foreground">{t.tickets(dateTickets)}</span>
          )}
          <ToggleGroup
            aria-label={t.modesLabel}
            size="sm"
            spacing={0}
            value={[mode]}
            onValueChange={(value: unknown[]) => {
              const next = value[0] as Mode | undefined;
              if (next) {
                setMode(next);
                setPicked(null);
              }
            }}
          >
            <ToggleGroupItem value="now">{t.modes.now}</ToggleGroupItem>
            <ToggleGroupItem value="dates">{t.modes.dates}</ToggleGroupItem>
          </ToggleGroup>
        </div>
      }
    >
      {failure && (
        <div className="px-4 pt-4">
          <FailureNotice failure={failure} />
        </div>
      )}
      {map === null ? (
        !failure && <p className="px-4 py-6 text-body text-muted-foreground">{t.loading}</p>
      ) : (
        <div className="relative min-h-0 flex-1">
          <div
            ref={scroller}
            className="flex h-full items-center justify-center-safe overflow-x-auto overflow-y-hidden md:bg-muted"
          >
            <div
              className="relative shrink-0 overflow-hidden select-none md:rounded-2xl md:shadow-sm"
              style={{ width: size.width, height: size.height }}
            >
              <svg
                viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
                width={size.width}
                height={size.height}
                role="img"
                aria-label={t.scene}
                className="block"
              >
                <MapArt time={timeOfDay(now)} labels={t.places} highlight={mode === "dates" ? "dates" : "everyday"} />
              </svg>
              {mode === "now" ? (
                <Idols
                  idols={idols}
                  people={people}
                  scale={scale}
                  picked={picked}
                  onPick={(id) => setPicked((was) => (was === id ? null : id))}
                />
              ) : (
                <Spots
                  dates={map.dates}
                  people={people}
                  scale={scale}
                  today={dayOf(now, timeZone)}
                  onSpot={setSpot}
                  onMeet={(characterId) => go(`/conversation/${encodeURIComponent(characterId)}?outing=1`)}
                />
              )}
            </div>
          </div>
          {mode === "dates" && map.dates.length > 0 && (
            <DateList dates={map.dates} people={people} />
          )}
          {chosen && mode === "now" ? (
            <IdolCard
              key={chosen.character_id}
              idol={chosen}
              her={people.get(chosen.character_id)}
              onClose={() => setPicked(null)}
              onChat={() => go(`/conversation/${encodeURIComponent(chosen.character_id)}`)}
              onMet={() => go(`/conversation/${encodeURIComponent(chosen.character_id)}?outing=1`)}
            />
          ) : (
            <p
              aria-live="polite"
              className="pointer-events-none absolute bottom-4 left-1/2 max-w-[calc(100%-2rem)] -translate-x-1/2 truncate rounded-full border bg-background/90 px-4 py-1.5 text-sm shadow-sm"
            >
              {hint}
            </p>
          )}
        </div>
      )}
      <TicketDialog
        kind={spot ? "date" : null}
        idea={spot ? t.ideas[spot] : undefined}
        characters={characters ?? []}
        catalog={new Map()}
        isWide={isWide}
        timeZone={timeZone}
        onClose={() => setSpot(null)}
        onDone={load}
        onGo={go}
      />
    </PageShell>
  );
}

/** 今天是哪天（YYYY-MM-DD），按 `timeZone`。 */
function dayOf(now: Date, timeZone: string): string {
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
}

/** 「现在」：每处的人立在那栋建筑上面排成一排，头像按钮。 */
function Idols({
  idols,
  people,
  scale,
  picked,
  onPick,
}: {
  idols: IdolOnMap[];
  people: Map<string, Character>;
  scale: number;
  picked: string | null;
  onPick: (id: string) => void;
}) {
  const size = Math.max(34, AVATAR * scale);
  return [...byPlace(idols)].flatMap(([place, here]) =>
    here.map((idol, i) => {
      const her = people.get(idol.character_id);
      if (!her) return null;
      const [x, y] = PLACE_AT[place];
      const [dx, dy] = fanned(i, here.length);
      const doing = idol.trip && place === "away" ? t.trip(idol.trip) : (scheduleText(idol) ?? "");
      const isPicked = picked === idol.character_id;
      return (
        <button
          key={idol.character_id}
          type="button"
          aria-label={t.idolLabel(her.name, t.places[place], doing)}
          aria-pressed={isPicked}
          onClick={() => onPick(idol.character_id)}
          className={cn(
            "absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-[3px] bg-background shadow-md transition-transform hover:z-10 hover:scale-110 focus-visible:z-10 focus-visible:scale-110 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            isPicked && "z-10 scale-115",
          )}
          style={{
            left: x * MAP_WIDTH * scale + dx * scale,
            top: y * MAP_HEIGHT * scale + dy * scale,
            width: size,
            height: size,
            borderColor: her.color ?? "white",
          }}
        >
          {her.avatar_url ? (
            // eslint-disable-next-line @next/next/no-img-element -- 头像是后端给的小图
            <img src={her.avatar_url} alt="" className="size-full rounded-full object-cover" draggable={false} />
          ) : (
            <span className="flex size-full items-center justify-center rounded-full text-sm font-semibold">
              {her.name.slice(0, 1)}
            </span>
          )}
        </button>
      );
    }),
  );
}

/** 「约会」：能约的每一处一个按钮；约好的约会挂在那一处旁边。 */
function Spots({
  dates,
  people,
  scale,
  today,
  onSpot,
  onMeet,
}: {
  dates: DateOnMap[];
  people: Map<string, Character>;
  scale: number;
  /** 今天（YYYY-MM-DD，按制作人的时区）：今天的约会点了就去赴约。 */
  today: string;
  onSpot: (spot: DateSpot) => void;
  onMeet: (characterId: string) => void;
}) {
  const size = Math.max(56, 150 * scale);
  return DATE_SPOTS.map((spot) => {
    const [x, y] = PLACE_AT[spot];
    const here = dates.filter((d) => d.spot === spot);
    return (
      <div
        key={spot}
        className="absolute -translate-x-1/2 -translate-y-1/2"
        style={{ left: x * MAP_WIDTH * scale, top: y * MAP_HEIGHT * scale }}
      >
        <button
          type="button"
          aria-label={t.spotLabel(t.places[spot])}
          onClick={() => onSpot(spot)}
          className="block animate-pulse rounded-full border-[3px] border-dashed border-pink-400/80 bg-pink-200/20 hover:animate-none hover:bg-pink-200/40 focus-visible:animate-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring motion-reduce:animate-none"
          style={{ width: size, height: size }}
        />
        {here.length > 0 && (
          <ul className="absolute top-full left-1/2 mt-1 flex -translate-x-1/2 flex-col items-center gap-1">
            {here.map((d) => {
              const her = people.get(d.character_id);
              if (!her) return null;
              const [, month, day] = d.day.split("-").map(Number);
              return (
                <li key={`${d.character_id}-${d.day}`}>
                  {d.day === today ? (
                    <Button size="sm" variant="her" className="h-7 gap-1 rounded-full pl-0.5 text-xs" onClick={() => onMeet(d.character_id)}>
                      <MarkedAvatar name={her.name} src={her.avatar_url} size="sm" />
                      {t.meetHer(her.name)}
                    </Button>
                  ) : (
                    <span
                      title={t.dateLine(her.name, month, day, d.what)}
                      className="flex items-center gap-1 rounded-full border bg-background/95 py-0.5 pr-2 pl-0.5 text-xs whitespace-nowrap shadow-sm"
                    >
                      <MarkedAvatar name={her.name} src={her.avatar_url} size="sm" />
                      {t.dateBadge(her.name, month, day)}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    );
  });
}

/** 点了头像以后底下那张小卡：她是谁、在哪、在做什么，去找她。 */
function IdolCard({
  idol,
  her,
  onClose,
  onChat,
  onMet,
}: {
  idol: IdolOnMap;
  her: Character | undefined;
  onClose: () => void;
  onChat: () => void;
  /** 偶遇到了（GLOSSARY.md「偶遇」）：去她的对话页，打开偶遇的画面。 */
  onMet: () => void;
}) {
  const [isGoing, setGoing] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  if (!her) return null;
  const place: Place = idol.place;
  const doing = scheduleText(idol);
  const canMeet = (ENCOUNTER_PLACES as readonly string[]).includes(place);
  const goThere = async () => {
    setGoing(true);
    setFailure(null);
    try {
      await encounter(idol.character_id);
      onMet();
    } catch (e) {
      setFailure(toFailure(e));
      setGoing(false);
    }
  };
  return (
    <div className="absolute bottom-4 left-1/2 flex w-[min(28rem,calc(100%-2rem))] -translate-x-1/2 flex-col gap-2 rounded-2xl border bg-background/95 p-3 shadow-md">
      {failure && <FailureNotice failure={failure} />}
      <div className="flex flex-wrap items-center gap-3">
        <MarkedAvatar name={her.name} src={her.avatar_url} size="lg" />
        <div className="min-w-32 flex-1">
          <p className="truncate text-body font-medium">{her.name}</p>
          <p className="truncate text-meta text-muted-foreground">
            {idol.trip && place === "away" ? t.trip(idol.trip) : t.places[place]}
            {doing ? ` · ${doing}` : ""}
          </p>
        </div>
        <div className="ml-auto flex items-center gap-2">
        {canMeet && (
          <Button size="sm" disabled={isGoing} onClick={() => void goThere()}>
            {isGoing && <Spinner data-icon="inline-start" />}
            {t.encounter}
          </Button>
        )}
        <Button size="sm" variant={canMeet ? "outline" : "default"} onClick={onChat}>
          {t.goChat}
        </Button>
        <Button variant="ghost" size="sm" onClick={onClose}>
          {t.close}
        </Button>
        </div>
      </div>
    </div>
  );
}

/** 「约会」时左上角一小块：约好的约会，哪天、和谁、做什么（地图上没有那一处的也在这里）。 */
function DateList({ dates, people }: { dates: DateOnMap[]; people: Map<string, Character> }) {
  return (
    <section
      aria-label={t.datesTitle}
      className="absolute top-3 left-3 max-h-[40%] w-[min(18rem,calc(100%-1.5rem))] overflow-y-auto rounded-xl border bg-background/95 p-3 shadow-sm"
    >
      <h2 className="mb-1 text-meta font-medium">{t.datesTitle}</h2>
      <ul className="flex flex-col gap-1">
        {dates.map((d) => {
          const her = people.get(d.character_id);
          if (!her) return null;
          const [, month, day] = d.day.split("-").map(Number);
          return (
            <li key={`${d.character_id}-${d.day}`} className="text-meta text-muted-foreground">
              {t.dateLine(her.name, month, day, d.what)}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

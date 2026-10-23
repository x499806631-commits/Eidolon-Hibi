"use client";

import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from "react";

import { hiddenBySwitch } from "@/components/app/AppNav";
import { FailureNotice } from "@/components/app/FailureNotice";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory, useWide } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { useMediaQuery } from "@/hooks/useMediaQuery";
import { copy } from "@/i18n";
import { photoImageUrl } from "@/lib/api";
import { hasUnseen } from "@/lib/conversationList";
import { unreadDiary } from "@/lib/diary";
import { toFailure, type Failure } from "@/lib/failure";
import {
  changeHome,
  drawRoom,
  getHome,
  roomPictureUrl,
  SHELF_SPACES,
  THING_GOES_TO,
  timeOfDay,
  type HomeState,
  type HomeThing,
  type Room,
  type Spot,
} from "@/lib/home";
import { getCabinet, keepsakeImageUrl } from "@/lib/keepsakes";
import { unreadLetters } from "@/lib/letters";
import { AlbumIcon, ArrangeIcon, DrawRoomIcon, MapIcon, SwitchRoomIcon } from "@/theme/icons";

import { AlbumDialog } from "./AlbumDialog";
import { PICTURE_HEIGHT, RoomScene, SCENE_HEIGHT, SCENE_WIDTH, sceneSize } from "./RoomScene";
import type { ShelfItem } from "./RoomThings";

const t = copy.home;
/** 多久看一次钟，换屋里的光（毫秒）。 */
const CLOCK_MS = 60_000;
const NEWS_THINGS = ["mailbox", "diary", "shelf", "phone"] as const;

/**
 * 制作人的家（`/home`，GLOSSARY.md「制作人的家」）：页首是「家」，右边「搬去大房子 / 一室公寓」、「出门看看」（去地图）
 * 和「相册」；下面整块是屋子。
 * 屋子按高度铺满，宽出来的（手机竖着拿时）可以左右划着看，一打开先停在中间。指着、Tab 到一样东西时，屋子底下的
 * 提示条说它是什么、有没有新的、点了会怎样；点了去对应的页，相框打开相册，窗户拉窗帘。
 */
export function HomePage() {
  const [home, setHome] = useState<HomeState | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [shelf, setShelf] = useState<ShelfItem[]>([]);
  const [newKeepsakes, setNewKeepsakes] = useState(false);
  const [hovered, setHovered] = useState<HomeThing | null>(null);
  const [isCurtainClosed, setCurtainClosed] = useState(false);
  const [isAlbumOpen, setAlbumOpen] = useState(false);
  const [now, setNow] = useState(() => new Date());
  const [isArranging, setArranging] = useState(false);
  const [isDrawing, setDrawing] = useState(false);
  const { characters, latest, preferences, onScreen, account, go } = useDirectory();
  const still = useMediaQuery("(prefers-reduced-motion: reduce)", false);
  const scroller = useRef<HTMLDivElement>(null);
  const isWide = useWide();
  const [space, setSpace] = useState({ width: 0, height: 0 });

  useEffect(() => {
    let current = true;
    getHome().then(
      (h) => current && setHome(h),
      (e) => current && setFailure(toFailure(e)),
    );
    getCabinet().then(
      (cabinet) => {
        if (!current) return;
        setNewKeepsakes(cabinet.keepsakes.some((k) => !k.seen));
        setShelf(
          cabinet.keepsakes.slice(0, SHELF_SPACES).map((k) => ({
            key: String(k.id),
            imageUrl: k.has_image ? keepsakeImageUrl(k) : null,
            emoji: k.emoji,
          })),
        );
      },
      () => {}, // 纪念品关着、读不到：架子空着
    );
    const timer = setInterval(() => setNow(new Date()), CLOCK_MS);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, []);

  // 量出屋子能占多大；宽出来时先停在屋子中间。
  const room = home?.room;
  useLayoutEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const measure = () => setSpace({ width: el.clientWidth, height: el.clientHeight });
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [room]);
  const hasPicture = home?.background != null;
  const size = sceneSize(space.width, space.height, isWide, SCENE_WIDTH / (hasPicture ? PICTURE_HEIGHT : SCENE_HEIGHT));
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2;
  }, [room, size.width]);

  const letters = useSyncExternalStore(unreadLetters.subscribe, unreadLetters.snapshot, () => 0) > 0;
  const diary = useSyncExternalStore(unreadDiary.subscribe, unreadDiary.snapshot, () => 0) > 0;
  const phone = characters !== null && hasUnseen(characters, latest, { preferences, onScreen });
  const features = account?.features;
  const off: Partial<Record<HomeThing, boolean>> = {
    mailbox: hiddenBySwitch("letters", features),
    diary: hiddenBySwitch("diary", features),
    shelf: hiddenBySwitch("keepsakes", features),
  };
  const notices: Partial<Record<HomeThing, boolean>> = {
    mailbox: letters && !off.mailbox,
    diary: diary && !off.diary,
    shelf: newKeepsakes && !off.shelf,
    phone,
  };
  const labels = labelsOf(notices);

  const onThing = (thing: HomeThing) => {
    const to = THING_GOES_TO[thing];
    if (to) go(to);
    else if (thing === "frame") setAlbumOpen(true);
    else if (thing === "window") setCurtainClosed((closed) => !closed);
  };

  const move = (to: Room) => {
    setArranging(false);
    setHome((before) => before && { ...before, room: to, background: null, layout: {} });
    changeHome({ room: to }).then(setHome, (e) => setFailure(toFailure(e)));
  };

  const draw = () => {
    setDrawing(true);
    setFailure(null);
    drawRoom().then(
      (h) => {
        setHome(h);
        setDrawing(false);
      },
      (e) => {
        setFailure(toFailure(e));
        setDrawing(false);
      },
    );
  };

  const place = (thing: HomeThing, spot: Spot) => {
    setHome((before) => before && { ...before, layout: { ...before.layout, [thing]: spot } });
    changeHome({ layout: { [thing]: spot } }).then(
      () => {},
      (e) => setFailure(toFailure(e)),
    );
  };

  const frame = (photoId: string | null) => {
    setHome((before) => before && { ...before, framed_photo: photoId });
    setAlbumOpen(false);
    changeHome({ framed_photo: photoId }).then(setHome, (e) => setFailure(toFailure(e)));
  };

  const other: Room = home?.room === "mansion" ? "apartment" : "mansion";
  const hint = isDrawing
    ? t.drawing
    : isArranging
      ? t.arrangeHint
      : hovered === null
        ? isWide
          ? t.hintIdle
          : t.hintSwipe
        : hintFor(hovered, labels[hovered], isCurtainClosed);

  return (
    <PageShell
      width="full"
      isInShell
      isScrollable={false}
      header={
        <div className="flex w-full items-center gap-2">
          <h1 className="flex-1 text-list-title">{t.title}</h1>
          {home && !isArranging && (
            <Button variant="outline" size="sm" disabled={isDrawing} onClick={() => move(other)} aria-label={t.moveTo(t.rooms[other])}>
              <SwitchRoomIcon />
              <span className="max-sm:hidden">{t.moveTo(t.rooms[other])}</span>
            </Button>
          )}
          {home?.can_draw && !isArranging && (
            <Button variant="outline" size="sm" disabled={isDrawing} onClick={draw} aria-label={hasPicture ? t.redraw : t.draw}>
              <DrawRoomIcon />
              <span className="max-sm:hidden">{isDrawing ? t.drawingShort : hasPicture ? t.redraw : t.draw}</span>
            </Button>
          )}
          {hasPicture && (
            <Button
              variant={isArranging ? "default" : "outline"}
              size="sm"
              disabled={isDrawing}
              onClick={() => setArranging((on) => !on)}
              aria-label={isArranging ? t.arrangeDone : t.arrange}
            >
              <ArrangeIcon />
              <span className="max-sm:hidden">{isArranging ? t.arrangeDone : t.arrange}</span>
            </Button>
          )}
          {!isArranging && (
            <Button variant="ghost" size="sm" onClick={() => go("/home/map")} aria-label={copy.map.open}>
              <MapIcon />
              <span className="max-sm:hidden">{copy.map.open}</span>
            </Button>
          )}
          {!isArranging && (
            <Button variant="ghost" size="sm" onClick={() => setAlbumOpen(true)} aria-label={t.album.title}>
              <AlbumIcon />
              <span className="max-sm:hidden">{t.album.title}</span>
            </Button>
          )}
        </div>
      }
    >
      {failure && (
        <div className="px-4 pt-4">
          <FailureNotice failure={failure} />
        </div>
      )}
      {home === null ? (
        !failure && <p className="px-4 py-6 text-body text-muted-foreground">{t.loading}</p>
      ) : (
        <div className="relative min-h-0 flex-1">
          <div ref={scroller} className="flex h-full items-center justify-center-safe overflow-x-auto overflow-y-hidden md:bg-muted">
            <RoomScene
              room={home.room}
              size={size}
              pictureUrl={home.background != null ? roomPictureUrl(home.room, home.background) : null}
              layout={home.layout}
              isArranging={isArranging}
              onPlace={place}
              time={timeOfDay(now)}
              sky={home.sky}
              isCurtainClosed={isCurtainClosed}
              framedPhotoUrl={home.framed_photo ? photoImageUrl(home.framed_photo) : null}
              shelf={shelf}
              notices={notices}
              off={off}
              labels={labels}
              sceneLabel={t.scene}
              today={{ month: now.getMonth() + 1, day: now.getDate(), weekday: t.weekdays[now.getDay()] }}
              still={still}
              onThing={onThing}
              onHover={setHovered}
            />
          </div>
          <p
            aria-live="polite"
            className="pointer-events-none absolute bottom-4 left-1/2 -translate-x-1/2 rounded-full border bg-background/90 px-4 py-1.5 text-sm whitespace-nowrap shadow-sm"
          >
            {hint}
          </p>
        </div>
      )}
      <AlbumDialog
        open={isAlbumOpen}
        onOpenChange={setAlbumOpen}
        framed={home?.framed_photo ?? null}
        characters={characters ?? []}
        onFrame={frame}
      />
    </PageShell>
  );
}

/** 每样东西的名字，有新的时接上一句：「信箱，有没读的信」。 */
function labelsOf(notices: Partial<Record<HomeThing, boolean>>): Record<HomeThing, string> {
  const said = {} as Record<HomeThing, string>;
  for (const thing of Object.keys(t.things) as HomeThing[]) {
    const news = NEWS_THINGS.find((n) => n === thing);
    said[thing] = news && notices[news] ? `${t.things[thing]}，${t.news[news]}` : t.things[thing];
  }
  return said;
}

/** 提示条上的一句：「信箱，有没读的信 · 看看她们写来的信」。 */
function hintFor(thing: HomeThing, label: string, isCurtainClosed: boolean): string {
  const does = thing === "window" ? t.hints.window[isCurtainClosed ? "closed" : "open"] : t.hints[thing];
  return `${label} · ${does}`;
}

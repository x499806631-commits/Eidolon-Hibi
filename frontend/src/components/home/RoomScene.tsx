"use client";

// 制作人的家的整间屋子（GLOSSARY.md「制作人的家」）：一张 1600×900 的 SVG 插画，一室公寓或大房子。
// 墙、地板、家具是画；信箱、日记本、纪念品架、手机、挂历、抽屉、相框、窗户是能点的（RoomThings）。
// 屋里罩一层跟着时候变的光，夜里台灯亮着。

import { useRef, useState, type PointerEvent, type ReactNode } from "react";

import { spotOf, type HomeThing, type Layout, type Room, type Sky, type Spot, type TimeOfDay } from "@/lib/home";

import { LAMP_GLOW, LIGHT, PALETTES, STAR, type RoomPalette } from "./homeArt";
import {
  CalendarArt,
  DiaryArt,
  DrawerArt,
  FrameArt,
  GiftBoxArt,
  MailboxArt,
  NoticeDot,
  PhoneArt,
  ShelfBoard,
  Thing,
  WindowArt,
  type ShelfItem,
} from "./RoomThings";

export const SCENE_WIDTH = 1600;
export const SCENE_HEIGHT = 900;
/** 画好的屋子：同合照一样的横图（1216×832），按 1600 宽算。 */
export const PICTURE_HEIGHT = Math.round((SCENE_WIDTH * 832) / 1216);

export type SceneProps = {
  room: Room;
  time: TimeOfDay;
  sky: Sky | null;
  isCurtainClosed: boolean;
  framedPhotoUrl: string | null;
  shelf: ShelfItem[];
  /** 哪些东西上有新的：没读的信、没读的日记、新的纪念品、没看过的搭话。 */
  notices: Partial<Record<HomeThing, boolean>>;
  /** 用户关掉了那项玩法的东西：照样画，不能点。 */
  off: Partial<Record<HomeThing, boolean>>;
  labels: Record<HomeThing, string>;
  /** 这间屋子的画（部署者的生图画的）；没画过是 null，用代码画的屋子。 */
  pictureUrl: string | null;
  /** 画好的屋子里东西摆在哪。 */
  layout: Layout;
  /** 布置中：东西拖着摆，不去哪里。 */
  isArranging: boolean;
  /** 摆好了一样东西（松手、方向键）。 */
  onPlace: (thing: HomeThing, spot: Spot) => void;
  /** 画多大（像素）：见 `sceneSize`。 */
  size: { width: number; height: number };
  /** 整间屋子读屏读的名字。 */
  sceneLabel: string;
  today: { month: number; day: number; weekday: string };
  /** 不喜欢动的用户（prefers-reduced-motion）：不跳、不闪、不下雨。 */
  still: boolean;
  onThing: (thing: HomeThing) => void;
  onHover: (thing: HomeThing | null) => void;
};

/** 屋子在 `width`×`height` 的地方画多大：宽屏整间放下、四周留 16px（像一个摆在桌上的模型）；窄屏（手机）
 * 按高度铺满，宽出来的左右划着看。 */
export function sceneSize(
  width: number,
  height: number,
  isWide: boolean,
  ratio: number = SCENE_WIDTH / SCENE_HEIGHT,
): { width: number; height: number } {
  if (isWide) {
    const w = Math.max(0, Math.min(width - 32, (height - 32) * ratio));
    return { width: w, height: w / ratio };
  }
  const h = Math.max(height, width / ratio);
  return { width: Math.max(width, h * ratio), height: h };
}

export function RoomScene(props: SceneProps) {
  const palette = PALETTES[props.room];
  const light = LIGHT[props.time];
  const isNight = props.time === "night";
  const height = props.pictureUrl ? PICTURE_HEIGHT : SCENE_HEIGHT;
  if (props.pictureUrl) return <PictureRoom {...props} pictureUrl={props.pictureUrl} palette={palette} />;
  return (
    <svg
      viewBox={`0 0 ${SCENE_WIDTH} ${height}`}
      width={props.size.width}
      height={props.size.height}
      className="block shrink-0 select-none md:rounded-2xl md:shadow-sm"
      role="group"
      aria-label={props.sceneLabel}
    >
      <defs>
        <filter id="home-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor={STAR} floodOpacity="0.95" />
        </filter>
        <radialGradient id="lamp-glow">
          <stop offset="0" stopColor={LAMP_GLOW} stopOpacity="0.55" />
          <stop offset="1" stopColor={LAMP_GLOW} stopOpacity="0" />
        </radialGradient>
      </defs>
      {props.room === "apartment" ? <Apartment {...props} palette={palette} /> : <Mansion {...props} palette={palette} />}
      {/* 一天里的光：罩在整间屋子上，不挡点击 */}
      <rect width={SCENE_WIDTH} height={SCENE_HEIGHT} fill={light.color} opacity={light.opacity} pointerEvents="none" style={{ mixBlendMode: "multiply" }} />
      {isNight && <LampLights room={props.room} />}
    </svg>
  );
}

/** 夜里亮着的灯：在光那层上面再提亮一圈。 */
function LampLights({ room }: { room: Room }) {
  const spots = room === "apartment" ? [[1490, 430, 260], [1000, 120, 320]] : [[880, 170, 380], [442, 560, 220], [910, 560, 220]];
  return (
    <g pointerEvents="none" style={{ mixBlendMode: "screen" }}>
      {spots.map(([cx, cy, r], i) => (
        <circle key={i} cx={cx} cy={cy} r={r} fill="url(#lamp-glow)" />
      ))}
    </g>
  );
}

type RoomProps = SceneProps & { palette: RoomPalette };

function Floor({ palette, top, plank }: { palette: RoomPalette; top: number; plank: number }) {
  const lines = Math.ceil((SCENE_HEIGHT - top) / plank);
  return (
    <g>
      <rect y={top} width={SCENE_WIDTH} height={SCENE_HEIGHT - top} fill={palette.floor} />
      {Array.from({ length: lines }, (_, i) => (
        <line key={i} x1={0} x2={SCENE_WIDTH} y1={top + (i + 1) * plank} y2={top + (i + 1) * plank} stroke={palette.floorLine} strokeWidth={3} />
      ))}
      {Array.from({ length: lines * 4 }, (_, i) => {
        const row = Math.floor(i / 4);
        const x = ((i % 4) * 420 + row * 170) % SCENE_WIDTH;
        return <line key={`v${i}`} x1={x} x2={x} y1={top + row * plank} y2={top + (row + 1) * plank} stroke={palette.floorLine} strokeWidth={3} />;
      })}
    </g>
  );
}

/** 一室公寓：左边门和信箱，窗下一张床，墙上挂历、相框、两块搁板，右边书桌带抽屉柜，地毯上一张矮桌放着手机。 */
function Apartment(p: RoomProps) {
  const { palette } = p;
  const floorTop = 620;
  const [top, bottom] = splitShelf(p.shelf, 3);
  return (
    <g>
      <rect width={SCENE_WIDTH} height={floorTop} fill={palette.wall} />
      {/* 墙纸上的竖条 */}
      {Array.from({ length: 20 }, (_, i) => (
        <rect key={i} x={i * 80 + 30} y={0} width={20} height={floorTop} fill={palette.wallShade} opacity={0.35} />
      ))}
      <rect y={0} width={SCENE_WIDTH} height={18} fill={palette.trim} />
      <Floor palette={palette} top={floorTop} plank={56} />
      <rect y={floorTop - 18} width={SCENE_WIDTH} height={18} fill={palette.trim} />

      {/* 吸顶灯 */}
      <line x1={1000} y1={18} x2={1000} y2={60} stroke={palette.ink} strokeWidth={3} />
      <path d="M950 60 h100 l-14 34 h-72 Z" fill={palette.paper} />

      {/* 门 */}
      <rect x={60} y={190} width={200} height={432} rx={4} fill={palette.trim} />
      <rect x={76} y={206} width={168} height={416} rx={3} fill={palette.woodLight} />
      <rect x={96} y={236} width={128} height={150} rx={6} fill={palette.wood} opacity={0.35} />
      <rect x={96} y={420} width={128} height={170} rx={6} fill={palette.wood} opacity={0.35} />
      <circle cx={222} cy={420} r={9} fill={palette.metal} />
      {/* 门口的鞋 */}
      <ellipse cx={130} cy={640} rx={26} ry={9} fill={palette.accent} />
      <ellipse cx={168} cy={646} rx={26} ry={9} fill={palette.accent} />

      <Thing thing="mailbox" label={p.labels.mailbox} isOff={p.off.mailbox} x={290} y={300} onActivate={p.onThing} onHover={p.onHover}>
        <MailboxArt palette={palette} hasLetters={Boolean(p.notices.mailbox)} still={p.still} brass={false} />
      </Thing>

      <Thing thing="window" label={p.labels.window} x={460} y={110} onActivate={p.onThing} onHover={p.onHover}>
        <WindowArt w={340} h={300} time={p.time} sky={p.sky} isClosed={p.isCurtainClosed} arched={false} palette={palette} still={p.still} />
      </Thing>

      {/* 窗下的床 */}
      <rect x={420} y={470} width={36} height={250} rx={8} fill={palette.woodDark} />
      <rect x={430} y={560} width={430} height={130} rx={18} fill={palette.paper} />
      <rect x={470} y={548} width={400} height={150} rx={20} fill={palette.fabric} />
      <path d="M470 600 h400" stroke={palette.fabricShade} strokeWidth={6} />
      <rect x={452} y={520} width={110} height={56} rx={22} fill={palette.paper} />
      <rect x={440} y={690} width={440} height={30} rx={6} fill={palette.wood} />

      <Thing thing="calendar" label={p.labels.calendar} isOff={p.off.calendar} x={880} y={150} onActivate={p.onThing} onHover={p.onHover}>
        <CalendarArt palette={palette} {...p.today} />
      </Thing>

      <Thing thing="frame" label={p.labels.frame} x={1040} y={120} onActivate={p.onThing} onHover={p.onHover}>
        <FrameArt palette={palette} w={150} h={196} photoUrl={p.framedPhotoUrl} gilded={false} />
      </Thing>

      <Thing thing="shelf" label={p.labels.shelf} isOff={p.off.shelf} x={1250} y={0} onActivate={p.onThing} onHover={p.onHover}>
        <g transform="translate(0 230)">
          <ShelfBoard palette={palette} w={290} items={top} glass={false} books={0} />
        </g>
        <g transform="translate(0 360)">
          <ShelfBoard palette={palette} w={290} items={bottom} glass={false} books={p.shelf.length > 3 ? 3 : 5} />
        </g>
        {p.notices.shelf && <NoticeDot cx={300} cy={140} still={p.still} />}
      </Thing>

      {/* 书桌 */}
      <rect x={1150} y={520} width={420} height={24} rx={6} fill={palette.wood} />
      <rect x={1166} y={544} width={22} height={210} fill={palette.woodDark} />
      {/* 台灯 */}
      <rect x={1474} y={500} width={40} height={10} rx={4} fill={palette.ink} />
      <line x1={1494} y1={500} x2={1474} y2={430} stroke={palette.ink} strokeWidth={6} strokeLinecap="round" />
      <path d="M1440 434 l64 -10 l-12 -40 l-44 6 Z" fill={palette.accent} />

      <Thing thing="diary" label={p.labels.diary} isOff={p.off.diary} x={1230} y={474} onActivate={p.onThing} onHover={p.onHover}>
        <DiaryArt palette={palette} />
        {p.notices.diary && <NoticeDot cx={112} cy={-6} still={p.still} />}
      </Thing>

      <Thing thing="drawer" label={p.labels.drawer} x={1390} y={544} onActivate={p.onThing} onHover={p.onHover}>
        <DrawerArt palette={palette} w={170} h={210} />
      </Thing>

      {/* 地毯和矮桌 */}
      <ellipse cx={1000} cy={800} rx={300} ry={70} fill={palette.rug} />
      <ellipse cx={1000} cy={800} rx={300} ry={70} fill="none" stroke={palette.rugEdge} strokeWidth={8} strokeDasharray="4 14" />
      <rect x={860} y={740} width={20} height={70} fill={palette.woodDark} />
      <rect x={1120} y={740} width={20} height={70} fill={palette.woodDark} />
      <rect x={840} y={716} width={320} height={30} rx={10} fill={palette.woodLight} />
      {/* 桌上的马克杯 */}
      <rect x={1080} y={680} width={34} height={38} rx={6} fill={palette.paper} />
      <path d="M1114 690 q16 6 0 18" fill="none" stroke={palette.paper} strokeWidth={6} />

      <Thing thing="phone" label={p.labels.phone} x={950} y={620} onActivate={p.onThing} onHover={p.onHover}>
        <g transform="scale(0.8) rotate(-60 34 56) translate(0 40)">
          <PhoneArt glowing={Boolean(p.notices.phone)} still={p.still} />
        </g>
        {p.notices.phone && <NoticeDot cx={96} cy={30} still={p.still} />}
      </Thing>
    </g>
  );
}

/** 大房子：高墙护墙板、水晶吊灯；左边双开门和铜信箱，拱顶高窗，壁炉上挂着金框照片，右边玻璃展示柜，
 * 前面一张写字台（日记、抽屉），沙发边的小圆桌上放着手机。 */
function Mansion(p: RoomProps) {
  const { palette } = p;
  const floorTop = 640;
  const shelves = [p.shelf.slice(0, 2), p.shelf.slice(2, 4), p.shelf.slice(4, 6)];
  return (
    <g>
      <rect width={SCENE_WIDTH} height={floorTop} fill={palette.wall} />
      {/* 护墙板 */}
      <rect y={420} width={SCENE_WIDTH} height={floorTop - 420} fill={palette.wallShade} />
      <rect y={414} width={SCENE_WIDTH} height={10} fill={palette.trim} />
      {Array.from({ length: 10 }, (_, i) => (
        <rect key={i} x={i * 160 + 20} y={446} width={120} height={160} rx={4} fill="none" stroke={palette.trim} strokeWidth={4} />
      ))}
      {/* 墙上的饰板 */}
      {Array.from({ length: 5 }, (_, i) => (
        <rect key={`u${i}`} x={i * 320 + 40} y={70} width={240} height={300} rx={6} fill="none" stroke={palette.trim} strokeWidth={3} opacity={0.8} />
      ))}
      <rect y={0} width={SCENE_WIDTH} height={30} fill={palette.trim} />
      <rect y={30} width={SCENE_WIDTH} height={8} fill={palette.accent} opacity={0.5} />
      <Floor palette={palette} top={floorTop} plank={52} />
      <rect y={floorTop - 16} width={SCENE_WIDTH} height={16} fill={palette.trim} />

      {/* 水晶吊灯 */}
      <line x1={880} y1={38} x2={880} y2={90} stroke={palette.metal} strokeWidth={4} />
      <path d="M800 100 q80 50 160 0" fill="none" stroke={palette.metal} strokeWidth={6} />
      {[800, 840, 880, 920, 960].map((x, i) => (
        <g key={x}>
          <rect x={x - 5} y={i % 2 ? 106 : 92} width={10} height={20} rx={3} fill={palette.paper} />
          <path d={`M${x} ${i % 2 ? 126 : 112} l-6 12 h12 Z`} fill={palette.glass} />
        </g>
      ))}

      {/* 双开门 */}
      <path d="M50 640 V250 a120 120 0 0 1 240 0 V640 Z" fill={palette.trim} />
      <path d="M66 640 V254 a104 104 0 0 1 208 0 V640 Z" fill={palette.wood} />
      <line x1={170} y1={150} x2={170} y2={640} stroke={palette.woodDark} strokeWidth={4} />
      {[90, 186].map((x) => (
        <g key={x}>
          <rect x={x} y={300} width={64} height={130} rx={4} fill="none" stroke={palette.woodDark} strokeWidth={3} />
          <rect x={x} y={460} width={64} height={150} rx={4} fill="none" stroke={palette.woodDark} strokeWidth={3} />
        </g>
      ))}
      <circle cx={158} cy={450} r={7} fill={palette.metal} />
      <circle cx={182} cy={450} r={7} fill={palette.metal} />

      <Thing thing="mailbox" label={p.labels.mailbox} isOff={p.off.mailbox} x={310} y={320} onActivate={p.onThing} onHover={p.onHover}>
        <MailboxArt palette={palette} hasLetters={Boolean(p.notices.mailbox)} still={p.still} brass />
      </Thing>

      <Thing thing="window" label={p.labels.window} x={460} y={90} onActivate={p.onThing} onHover={p.onHover}>
        <WindowArt w={240} h={410} time={p.time} sky={p.sky} isClosed={p.isCurtainClosed} arched palette={palette} still={p.still} />
      </Thing>

      {/* 壁炉 */}
      <rect x={760} y={380} width={300} height={22} rx={4} fill={palette.trim} />
      <rect x={780} y={402} width={260} height={222} fill={palette.trim} />
      <path d="M840 624 V510 a70 70 0 0 1 140 0 V624 Z" fill={palette.ink} />
      {p.time === "night" || p.time === "evening" ? (
        <g>
          <path d="M870 624 q20 -70 40 -30 q14 -50 34 0 q16 -40 30 30 Z" fill={palette.accent} />
          <path d="M890 624 q14 -40 26 -14 q10 -30 22 0 q10 -20 20 14 Z" fill={STAR} />
        </g>
      ) : (
        <g>
          <rect x={870} y={600} width={80} height={14} rx={6} fill={palette.woodLight} />
          <rect x={880} y={588} width={70} height={14} rx={6} fill={palette.wood} />
        </g>
      )}
      {/* 壁炉台上的烛台 */}
      <rect x={790} y={340} width={14} height={40} fill={palette.paper} />
      <rect x={1016} y={340} width={14} height={40} fill={palette.paper} />
      <path d="M797 330 q-5 6 0 10 q5 -4 0 -10 Z" fill={palette.accent} />
      <path d="M1023 330 q-5 6 0 10 q5 -4 0 -10 Z" fill={palette.accent} />

      <Thing thing="frame" label={p.labels.frame} x={830} y={130} onActivate={p.onThing} onHover={p.onHover}>
        <FrameArt palette={palette} w={160} h={210} photoUrl={p.framedPhotoUrl} gilded />
      </Thing>

      <Thing thing="calendar" label={p.labels.calendar} isOff={p.off.calendar} x={1090} y={170} onActivate={p.onThing} onHover={p.onHover}>
        <CalendarArt palette={palette} {...p.today} />
      </Thing>

      <Thing thing="shelf" label={p.labels.shelf} isOff={p.off.shelf} x={1250} y={110} onActivate={p.onThing} onHover={p.onHover}>
        {/* 玻璃展示柜 */}
        <rect x={-14} y={-14} width={318} height={530} rx={8} fill={palette.wood} />
        <rect x={6} y={6} width={278} height={490} rx={4} fill={palette.glass} opacity={0.55} />
        <path d="M-24 -14 h338 l-16 -26 h-306 Z" fill={palette.woodDark} />
        {shelves.map((items, i) => (
          <g key={i} transform={`translate(30 ${150 + i * 160})`}>
            <ShelfBoard palette={palette} w={230} items={items} glass books={items.length < 2 ? 4 : 1} />
          </g>
        ))}
        <rect x={6} y={6} width={30} height={490} fill={palette.paper} opacity={0.18} />
        {p.notices.shelf && <NoticeDot cx={300} cy={-40} still={p.still} />}
      </Thing>

      {/* 大地毯 */}
      <rect x={520} y={700} width={720} height={170} rx={20} fill={palette.rug} />
      <rect x={540} y={716} width={680} height={138} rx={14} fill="none" stroke={palette.rugEdge} strokeWidth={6} />

      {/* 沙发 */}
      <rect x={640} y={650} width={420} height={110} rx={30} fill={palette.fabric} />
      <rect x={620} y={690} width={460} height={110} rx={30} fill={palette.fabricShade} />
      <rect x={690} y={700} width={150} height={60} rx={20} fill={palette.fabric} />
      <rect x={860} y={700} width={150} height={60} rx={20} fill={palette.fabric} />
      <rect x={640} y={796} width={16} height={30} fill={palette.woodDark} />
      <rect x={1044} y={796} width={16} height={30} fill={palette.woodDark} />

      {/* 小圆桌 */}
      <ellipse cx={1180} cy={720} rx={90} ry={22} fill={palette.woodLight} />
      <rect x={1172} y={720} width={16} height={110} fill={palette.woodDark} />
      <ellipse cx={1180} cy={830} rx={50} ry={10} fill={palette.woodDark} />
      <Thing thing="phone" label={p.labels.phone} x={1130} y={640} onActivate={p.onThing} onHover={p.onHover}>
        <g transform="scale(0.7) rotate(-62 34 56) translate(0 30)">
          <PhoneArt glowing={Boolean(p.notices.phone)} still={p.still} />
        </g>
        {p.notices.phone && <NoticeDot cx={92} cy={26} still={p.still} />}
      </Thing>

      {/* 写字台 */}
      <rect x={60} y={620} width={430} height={22} rx={6} fill={palette.wood} />
      <rect x={76} y={642} width={20} height={200} fill={palette.woodDark} />
      <rect x={420} y={606} width={44} height={12} rx={4} fill={palette.metal} />
      <rect x={437} y={540} width={10} height={70} fill={palette.metal} />
      <path d="M402 544 h80 l-16 -46 h-48 Z" fill={palette.fabric} />
      <Thing thing="diary" label={p.labels.diary} isOff={p.off.diary} x={110} y={574} onActivate={p.onThing} onHover={p.onHover}>
        <DiaryArt palette={palette} />
        {p.notices.diary && <NoticeDot cx={112} cy={-6} still={p.still} />}
      </Thing>
      <Thing thing="drawer" label={p.labels.drawer} x={300} y={642} onActivate={p.onThing} onHover={p.onHover}>
        <DrawerArt palette={palette} w={176} h={200} />
      </Thing>
    </g>
  );
}

/** 公寓的两块搁板：上面摆 `first` 件，其余摆下面。 */
function splitShelf(items: ShelfItem[], first: number): [ShelfItem[], ShelfItem[]] {
  return [items.slice(0, first), items.slice(first)];
}

/** 画好的屋子里每样东西：从它的位置（中心）到它画的原点差多少，怎么画。 */
const PICTURE_THINGS: Record<HomeThing, { offset: [number, number] }> = {
  mailbox: { offset: [-42, -70] },
  window: { offset: [-100, -80] },
  calendar: { offset: [-55, -70] },
  frame: { offset: [-65, -85] },
  shelf: { offset: [-125, 35] },
  diary: { offset: [-60, -24] },
  drawer: { offset: [-60, -55] },
  phone: { offset: [-24, -40] },
};

/**
 * 画好的屋子：部署者的生图画的一张屋子（铺满），上面摆着能点的东西，各在用户摆的地方（没摆过的沿墙摆着）。
 * 布置中按住一样东西拖到别处，松手就记下；方向键也能挪。光照同代码画的屋子，夜里只压暗、不点灯。
 */
function PictureRoom(p: SceneProps & { pictureUrl: string; palette: RoomPalette }) {
  const { palette } = p;
  const svg = useRef<SVGSVGElement>(null);
  const [dragging, setDragging] = useState<{ thing: HomeThing; spot: Spot } | null>(null);
  const light = LIGHT[p.time];
  const H = PICTURE_HEIGHT;

  const spotAt = (event: PointerEvent): Spot | null => {
    const matrix = svg.current?.getScreenCTM();
    if (!svg.current || !matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return [clamp(point.x / SCENE_WIDTH), clamp(point.y / H)];
  };
  const grab = (thing: HomeThing, event: PointerEvent<SVGGElement>) => {
    event.preventDefault();
    svg.current?.setPointerCapture(event.pointerId);
    setDragging({ thing, spot: spotOf(p.layout, thing) });
  };
  const move = (event: PointerEvent<SVGSVGElement>) => {
    if (!dragging) return;
    const spot = spotAt(event);
    if (spot) setDragging({ thing: dragging.thing, spot });
  };
  const drop = () => {
    if (!dragging) return;
    p.onPlace(dragging.thing, dragging.spot);
    setDragging(null);
  };
  const nudge = (thing: HomeThing, dx: number, dy: number) => {
    const [x, y] = spotOf(p.layout, thing);
    p.onPlace(thing, [clamp(x + dx), clamp(y + dy)]);
  };

  const at = (thing: HomeThing): { x: number; y: number } => {
    const [fx, fy] = dragging?.thing === thing ? dragging.spot : spotOf(p.layout, thing);
    const [dx, dy] = PICTURE_THINGS[thing].offset;
    return { x: fx * SCENE_WIDTH + dx, y: fy * H + dy };
  };
  const thing = (name: HomeThing, art: ReactNode) => (
    <Thing
      key={name}
      thing={name}
      label={p.labels[name]}
      isOff={p.off[name]}
      {...at(name)}
      onActivate={p.onThing}
      onHover={p.onHover}
      isArranging={p.isArranging}
      onGrab={grab}
      onNudge={nudge}
    >
      {art}
    </Thing>
  );
  // 拖着的那样画在最上面。
  const order: HomeThing[] = ["window", "frame", "calendar", "shelf", "mailbox", "drawer", "diary", "phone"];
  const drawn = dragging ? [...order.filter((t) => t !== dragging.thing), dragging.thing] : order;
  const arts: Record<HomeThing, ReactNode> = {
    mailbox: <MailboxArt palette={palette} hasLetters={Boolean(p.notices.mailbox)} still={p.still} brass={p.room === "mansion"} />,
    window: (
      <g transform="scale(0.85)">
        <WindowArt w={236} h={190} time={p.time} sky={p.sky} isClosed={p.isCurtainClosed} arched={false} palette={palette} still={p.still} />
      </g>
    ),
    calendar: <CalendarArt palette={palette} {...p.today} />,
    frame: <FrameArt palette={palette} w={130} h={170} photoUrl={p.framedPhotoUrl} gilded={p.room === "mansion"} />,
    shelf: (
      <g>
        <ShelfBoard palette={palette} w={250} items={p.shelf.slice(0, 3)} glass={false} books={p.shelf.length < 3 ? 3 - p.shelf.length : 0} />
        {p.notices.shelf && <NoticeDot cx={262} cy={-80} still={p.still} />}
      </g>
    ),
    diary: (
      <g>
        <DiaryArt palette={palette} />
        {p.notices.diary && <NoticeDot cx={112} cy={-6} still={p.still} />}
      </g>
    ),
    drawer: <GiftBoxArt palette={palette} />,
    phone: (
      <g>
        <g transform="scale(0.7)">
          <PhoneArt glowing={Boolean(p.notices.phone)} still={p.still} />
        </g>
        {p.notices.phone && <NoticeDot cx={56} cy={0} still={p.still} />}
      </g>
    ),
  };
  return (
    <svg
      ref={svg}
      viewBox={`0 0 ${SCENE_WIDTH} ${H}`}
      width={p.size.width}
      height={p.size.height}
      className="block shrink-0 touch-pan-x select-none md:rounded-2xl md:shadow-sm"
      role="group"
      aria-label={p.sceneLabel}
      onPointerMove={move}
      onPointerUp={drop}
      onPointerCancel={() => setDragging(null)}
    >
      <defs>
        <filter id="home-glow" x="-20%" y="-20%" width="140%" height="140%">
          <feDropShadow dx="0" dy="0" stdDeviation="6" floodColor={STAR} floodOpacity="0.95" />
        </filter>
      </defs>
      <image href={p.pictureUrl} width={SCENE_WIDTH} height={H} preserveAspectRatio="xMidYMid slice" />
      {drawn.map((name) => thing(name, arts[name]))}
      <rect width={SCENE_WIDTH} height={H} fill={light.color} opacity={light.opacity} pointerEvents="none" style={{ mixBlendMode: "multiply" }} />
    </svg>
  );
}

function clamp(n: number): number {
  return Math.min(1, Math.max(0, n));
}

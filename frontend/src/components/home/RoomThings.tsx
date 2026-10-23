"use client";

// 制作人的家里能点的东西，各自画成一小组 SVG（GLOSSARY.md「制作人的家」）。每样东西画在自己的坐标里
// （左上角是原点），由屋子（RoomScene）摆到墙上、桌上。颜色见 homeArt。

import type { KeyboardEvent, PointerEvent, ReactNode } from "react";

import type { HomeThing, Sky, TimeOfDay } from "@/lib/home";
import { cn } from "@/lib/utils";

import {
  CLOUD,
  DIARY_COVER,
  DIARY_SPINE,
  ENVELOPE,
  KEY_GOLD,
  NOTICE,
  PHONE_BODY,
  PLACEHOLDER,
  RAIN,
  RIBBON,
  SCREEN_GLOW,
  STAR,
  skyColors,
  type RoomPalette,
} from "./homeArt";

type ThingProps = {
  thing: HomeThing;
  label: string;
  /** 关掉了这项玩法的：照样画，不能点。 */
  isOff?: boolean;
  x: number;
  y: number;
  onActivate: (thing: HomeThing) => void;
  onHover: (thing: HomeThing | null) => void;
  /** 布置中：点了不去哪里，按住拖到别处，方向键挪一点。 */
  isArranging?: boolean;
  onGrab?: (thing: HomeThing, event: PointerEvent<SVGGElement>) => void;
  onNudge?: (thing: HomeThing, dx: number, dy: number) => void;
  children: ReactNode;
};

/** 布置中按一下方向键挪多少（整张画的几分之几）。 */
const NUDGE = 0.01;
const ARROWS: Record<string, [number, number]> = {
  ArrowLeft: [-NUDGE, 0],
  ArrowRight: [NUDGE, 0],
  ArrowUp: [0, -NUDGE],
  ArrowDown: [0, NUDGE],
};

/**
 * 能点的一样东西：读屏是一个按钮，键盘 Tab 得到，回车或空格点它。指上去、Tab 到时整组往上浮一点、描一圈白光
 * （`#home-glow`），屋子底下的提示条说这是什么。
 */
export function Thing({
  thing,
  label,
  isOff = false,
  x,
  y,
  onActivate,
  onHover,
  isArranging = false,
  onGrab,
  onNudge,
  children,
}: ThingProps) {
  if (isOff && !isArranging) {
    return (
      <g transform={`translate(${x} ${y})`} aria-hidden>
        {children}
      </g>
    );
  }
  const onKeyDown = (event: KeyboardEvent<SVGGElement>) => {
    const arrow = ARROWS[event.key];
    if (isArranging && arrow) {
      event.preventDefault();
      onNudge?.(thing, arrow[0], arrow[1]);
    } else if (!isArranging && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      onActivate(thing);
    }
  };
  return (
    <g transform={`translate(${x} ${y})`}>
      <g
        role="button"
        tabIndex={0}
        aria-label={label}
        className={cn("group outline-none", isArranging ? "cursor-move touch-none" : "cursor-pointer")}
        onClick={() => !isArranging && onActivate(thing)}
        onPointerDown={(event) => isArranging && onGrab?.(thing, event)}
        onKeyDown={onKeyDown}
        onMouseEnter={() => onHover(thing)}
        onMouseLeave={() => onHover(null)}
        onFocus={() => onHover(thing)}
        onBlur={() => onHover(null)}
      >
        <title>{label}</title>
        <g
          className={cn(
            isArranging
              ? "[filter:url(#home-glow)]"
              : "transition-transform duration-200 group-hover:-translate-y-1.5 group-hover:[filter:url(#home-glow)] group-focus-visible:-translate-y-1.5 group-focus-visible:[filter:url(#home-glow)]",
          )}
        >
          {children}
        </g>
      </g>
    </g>
  );
}

/** 有新东西时的小圆点，一跳一跳的（不喜欢动的用户不跳）。 */
export function NoticeDot({ cx, cy, still }: { cx: number; cy: number; still: boolean }) {
  return (
    <g>
      <circle cx={cx} cy={cy} r={11} fill={NOTICE} stroke={ENVELOPE} strokeWidth={3} />
      {!still && (
        <animateTransform attributeName="transform" type="translate" values="0 0; 0 -6; 0 0" dur="1.4s" repeatCount="indefinite" />
      )}
    </g>
  );
}

/** 窗户：窗外的天（跟着时候和天气），窗框，两边的窗帘（拉上时合到中间）。`arched` 是大房子的拱顶高窗。 */
export function WindowArt({
  w,
  h,
  time,
  sky,
  isClosed,
  arched,
  palette,
  still,
}: {
  w: number;
  h: number;
  time: TimeOfDay;
  sky: Sky | null;
  isClosed: boolean;
  arched: boolean;
  palette: RoomPalette;
  still: boolean;
}) {
  const [top, bottom] = skyColors(time, sky);
  const id = arched ? "win-arched" : "win-square";
  const r = w / 2;
  const pane = arched
    ? `M0 ${r} A${r} ${r} 0 0 1 ${w} ${r} L${w} ${h} L0 ${h} Z`
    : `M0 0 H${w} V${h} H0 Z`;
  const isNight = time === "night";
  const clouds = sky === "cloudy" || sky === "rain" || sky === "storm" || sky === "snow" || (sky === null && time === "day") || sky === "clear";
  const thick = sky === "cloudy" || sky === "rain" || sky === "storm" || sky === "snow";
  const falling = sky === "rain" || sky === "storm" ? "rain" : sky === "snow" ? "snow" : null;
  return (
    <g>
      <defs>
        <linearGradient id={`${id}-sky`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={top} />
          <stop offset="1" stopColor={bottom} />
        </linearGradient>
        <clipPath id={`${id}-clip`}>
          <path d={pane} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${id}-clip)`}>
        <rect width={w} height={h} fill={`url(#${id}-sky)`} />
        {/* 远处的街景剪影 */}
        <path
          d={`M0 ${h * 0.82} h${w * 0.12} v${-h * 0.14} h${w * 0.1} v${h * 0.06} h${w * 0.14} v${-h * 0.2} h${w * 0.12} v${h * 0.12} h${w * 0.16} v${-h * 0.1} h${w * 0.12} v${h * 0.16} h${w * 0.24} V${h} H0 Z`}
          fill={palette.ink}
          opacity={isNight ? 0.75 : 0.18}
        />
        {isNight &&
          [
            [0.18, 0.84],
            [0.27, 0.78],
            [0.47, 0.72],
            [0.52, 0.8],
            [0.74, 0.86],
            [0.66, 0.76],
          ].map(([fx, fy], i) => (
            <rect key={i} x={w * fx} y={h * fy} width={8} height={10} rx={1.5} fill={STAR} opacity={0.85} />
          ))}
        {isNight && !thick && (
          <g>
            <circle cx={w * 0.72} cy={h * 0.22} r={28} fill={STAR} />
            <circle cx={w * 0.72 + 12} cy={h * 0.22 - 8} r={24} fill={top} />
            {[
              [0.15, 0.15],
              [0.32, 0.3],
              [0.48, 0.12],
              [0.86, 0.4],
              [0.24, 0.48],
              [0.58, 0.38],
            ].map(([fx, fy], i) => (
              <circle key={i} cx={w * fx} cy={h * fy} r={2.5} fill={STAR}>
                {!still && <animate attributeName="opacity" values="1;0.3;1" dur={`${2 + i * 0.4}s`} repeatCount="indefinite" />}
              </circle>
            ))}
          </g>
        )}
        {!isNight && !thick && <circle cx={w * 0.78} cy={h * 0.2} r={30} fill={STAR} opacity={time === "evening" ? 0.9 : 0.75} />}
        {clouds && (
          <g opacity={isNight ? 0.25 : thick ? 0.95 : 0.8}>
            {(thick
              ? [
                  [0.2, 0.2, 1.3],
                  [0.6, 0.15, 1.5],
                  [0.95, 0.3, 1.2],
                  [0.35, 0.4, 1.1],
                ]
              : [
                  [0.25, 0.25, 0.8],
                  [0.62, 0.42, 0.6],
                ]
            ).map(([fx, fy, s], i) => (
              <g key={i} transform={`translate(${w * fx} ${h * fy}) scale(${s})`}>
                <g>
                  <ellipse cx={0} cy={0} rx={46} ry={18} fill={thick ? palette.wallShade : CLOUD} />
                  <circle cx={-14} cy={-12} r={20} fill={thick ? palette.wallShade : CLOUD} />
                  <circle cx={14} cy={-16} r={24} fill={thick ? palette.wallShade : CLOUD} />
                  {!still && (
                    <animateTransform attributeName="transform" type="translate" values="-6 0; 6 0; -6 0" dur={`${9 + i * 2}s`} repeatCount="indefinite" />
                  )}
                </g>
              </g>
            ))}
          </g>
        )}
        {sky === "fog" && <rect width={w} height={h} fill={CLOUD} opacity={0.45} />}
        {falling === "rain" && (
          <g stroke={RAIN} strokeWidth={2.5} strokeLinecap="round" opacity={0.8}>
            <g>
              {Array.from({ length: 18 }, (_, i) => {
                const fx = ((i * 37) % 100) / 100;
                const fy = ((i * 53) % 100) / 100;
                return <line key={i} x1={w * fx} y1={h * fy - 40} x2={w * fx - 6} y2={h * fy - 12} />;
              })}
              {!still && <animateTransform attributeName="transform" type="translate" values="0 -40; 0 60" dur="0.7s" repeatCount="indefinite" />}
            </g>
          </g>
        )}
        {falling === "snow" && (
          <g fill={CLOUD} opacity={0.9}>
            <g>
              {Array.from({ length: 22 }, (_, i) => {
                const fx = ((i * 41) % 100) / 100;
                const fy = ((i * 29) % 100) / 100;
                return <circle key={i} cx={w * fx} cy={h * fy - 30} r={3 + (i % 3)} />;
              })}
              {!still && <animateTransform attributeName="transform" type="translate" values="0 -30; 8 50" dur="4s" repeatCount="indefinite" />}
            </g>
          </g>
        )}
        {sky === "storm" && !still && (
          <rect width={w} height={h} fill={STAR} opacity={0}>
            <animate attributeName="opacity" values="0;0;0;0.5;0;0.3;0" dur="6s" repeatCount="indefinite" />
          </rect>
        )}
      </g>
      {/* 窗框和十字窗棂 */}
      <path d={pane} fill="none" stroke={palette.trim} strokeWidth={16} />
      <line x1={w / 2} y1={arched ? 0 : 0} x2={w / 2} y2={h} stroke={palette.trim} strokeWidth={10} />
      <line x1={0} y1={h * 0.55} x2={w} y2={h * 0.55} stroke={palette.trim} strokeWidth={10} />
      <rect x={-24} y={h} width={w + 48} height={16} rx={4} fill={palette.trim} />
      {/* 窗帘杆、窗帘 */}
      <rect x={-50} y={arched ? -28 : -30} width={w + 100} height={9} rx={4.5} fill={arched ? palette.metal : palette.woodDark} />
      <Curtain x={-44} side="left" width={w / 2 + 44} height={h + 40} isClosed={isClosed} palette={palette} arched={arched} />
      <Curtain x={w / 2} side="right" width={w / 2 + 44} height={h + 40} isClosed={isClosed} palette={palette} arched={arched} />
    </g>
  );
}

function Curtain({
  x,
  side,
  width,
  height,
  isClosed,
  palette,
  arched,
}: {
  x: number;
  side: "left" | "right";
  width: number;
  height: number;
  isClosed: boolean;
  palette: RoomPalette;
  arched: boolean;
}) {
  const folds = 5;
  return (
    <g
      className="transition-transform duration-700 ease-out"
      style={{
        transformBox: "fill-box",
        transformOrigin: side === "left" ? "left" : "right",
        transform: isClosed ? "scaleX(1)" : "scaleX(0.26)",
      }}
    >
      <rect x={x} y={arched ? -26 : -28} width={width} height={height} fill={palette.fabric} />
      {Array.from({ length: folds }, (_, i) => (
        <rect
          key={i}
          x={x + (width / folds) * i + width / folds / 2 - 4}
          y={arched ? -26 : -28}
          width={8}
          height={height}
          fill={palette.fabricShade}
          opacity={0.7}
        />
      ))}
      {arched && (
        <rect x={x} y={height - 60} width={width} height={14} fill={palette.accent} opacity={0.9} />
      )}
    </g>
  );
}

/** 信箱：挂在门边的墙上的小邮筒；有没读的信时，投信口露出一封信、角上一个点。 */
export function MailboxArt({ palette, hasLetters, still, brass }: { palette: RoomPalette; hasLetters: boolean; still: boolean; brass: boolean }) {
  const body = brass ? palette.metal : palette.accent;
  return (
    <g>
      <rect x={0} y={20} width={84} height={100} rx={12} fill={body} />
      <rect x={0} y={20} width={84} height={22} rx={10} fill={palette.ink} opacity={0.18} />
      <rect x={14} y={54} width={56} height={8} rx={4} fill={palette.ink} opacity={0.55} />
      <rect x={30} y={78} width={24} height={26} rx={4} fill={palette.paper} opacity={0.85} />
      <path d="M33 84 h18 M33 91 h18 M33 98 h12" stroke={palette.ink} strokeWidth={2} opacity={0.5} />
      {hasLetters && (
        <g>
          <g transform="translate(16 30) rotate(-8)">
            <rect width={52} height={34} rx={3} fill={ENVELOPE} stroke={palette.ink} strokeOpacity={0.25} strokeWidth={2} />
            <path d="M2 3 L26 20 L50 3" fill="none" stroke={palette.ink} strokeOpacity={0.3} strokeWidth={2} />
            <circle cx={26} cy={22} r={5} fill={NOTICE} />
          </g>
          <NoticeDot cx={84} cy={18} still={still} />
        </g>
      )}
    </g>
  );
}

/** 挂历：今天的日子大大地写着，月份那一条是红的。 */
export function CalendarArt({ palette, month, day, weekday }: { palette: RoomPalette; month: number; day: number; weekday: string }) {
  return (
    <g>
      <line x1={55} y1={-14} x2={55} y2={6} stroke={palette.ink} strokeWidth={2} />
      <circle cx={55} cy={-16} r={4} fill={palette.metal} />
      <rect x={0} y={0} width={110} height={140} rx={6} fill={palette.paper} stroke={palette.trim} strokeWidth={3} />
      <rect x={0} y={0} width={110} height={34} rx={6} fill={palette.accent} />
      <text x={55} y={24} textAnchor="middle" fontSize={18} fontWeight={700} fill={palette.paper}>
        {month}月
      </text>
      <text x={55} y={96} textAnchor="middle" fontSize={50} fontWeight={700} fill={palette.ink}>
        {day}
      </text>
      <text x={55} y={124} textAnchor="middle" fontSize={16} fill={palette.ink} opacity={0.7}>
        {weekday}
      </text>
    </g>
  );
}

/** 相框：挂着用户挑的照片；没挂时是空的衬纸，中间一颗小心。 */
export function FrameArt({ palette, w, h, photoUrl, gilded }: { palette: RoomPalette; w: number; h: number; photoUrl: string | null; gilded: boolean }) {
  const border = gilded ? 18 : 12;
  const id = `frame-${w}-${h}`;
  return (
    <g>
      <defs>
        <clipPath id={id}>
          <rect x={border} y={border} width={w - border * 2} height={h - border * 2} />
        </clipPath>
      </defs>
      <rect x={4} y={8} width={w} height={h} rx={4} fill={palette.ink} opacity={0.15} />
      <rect width={w} height={h} rx={4} fill={gilded ? palette.metal : palette.wood} />
      {gilded && <rect x={6} y={6} width={w - 12} height={h - 12} rx={3} fill="none" stroke={palette.accent} strokeWidth={3} />}
      <rect x={border} y={border} width={w - border * 2} height={h - border * 2} fill={PLACEHOLDER} />
      {photoUrl ? (
        <image href={photoUrl} x={border} y={border} width={w - border * 2} height={h - border * 2} preserveAspectRatio="xMidYMid slice" clipPath={`url(#${id})`} />
      ) : (
        <path
          transform={`translate(${w / 2} ${h / 2}) scale(1.6)`}
          d="M0 6 C-10 -2 -10 -10 -4 -10 C-1 -10 0 -7 0 -6 C0 -7 1 -10 4 -10 C10 -10 10 -2 0 6 Z"
          fill={palette.accent}
          opacity={0.45}
        />
      )}
    </g>
  );
}

/** 纪念品架上的一件：有图是她的图，裁成圆角方块；没图是 emoji。 */
export type ShelfItem = { key: string; imageUrl: string | null; emoji: string };

/** 一块搁板，上面并排站着几件纪念品，再加几本书凑数。 */
export function ShelfBoard({
  palette,
  w,
  items,
  glass,
  books,
}: {
  palette: RoomPalette;
  w: number;
  items: ShelfItem[];
  glass: boolean;
  books: number;
}) {
  const size = 66;
  const gap = 14;
  return (
    <g>
      {Array.from({ length: books }, (_, i) => (
        <rect
          key={`b${i}`}
          x={w - 20 - (i + 1) * 18}
          y={-58 - (i % 2) * 8}
          width={15}
          height={58 + (i % 2) * 8}
          rx={2}
          fill={[palette.fabric, palette.accent, palette.woodLight, palette.fabricShade][i % 4]}
        />
      ))}
      {items.map((item, i) => {
        const x = 10 + i * (size + gap);
        const clip = `shelf-${item.key}`;
        return (
          <g key={item.key} transform={`translate(${x} ${-size - 4})`}>
            <defs>
              <clipPath id={clip}>
                <rect width={size} height={size} rx={10} />
              </clipPath>
            </defs>
            <rect x={3} y={5} width={size} height={size} rx={10} fill={palette.ink} opacity={0.15} />
            <rect width={size} height={size} rx={10} fill={palette.paper} />
            {item.imageUrl ? (
              <image href={item.imageUrl} width={size} height={size} preserveAspectRatio="xMidYMid slice" clipPath={`url(#${clip})`} />
            ) : (
              <text x={size / 2} y={size / 2 + 13} textAnchor="middle" fontSize={36}>
                {item.emoji}
              </text>
            )}
          </g>
        );
      })}
      <rect x={-8} y={0} width={w + 16} height={glass ? 8 : 14} rx={3} fill={glass ? palette.glass : palette.wood} opacity={glass ? 0.9 : 1} />
      {!glass && <rect x={-8} y={12} width={w + 16} height={4} fill={palette.woodDark} opacity={0.6} />}
    </g>
  );
}

/** 日记本：粉色封面、金色的锁，钥匙挂在旁边。 */
export function DiaryArt({ palette }: { palette: RoomPalette }) {
  return (
    <g>
      <rect x={4} y={34} width={104} height={14} rx={3} fill={palette.paper} />
      <path d="M6 38 h100 M6 42 h100" stroke={palette.trim} strokeWidth={1.5} />
      <rect x={0} y={0} width={110} height={38} rx={6} fill={DIARY_COVER} />
      <rect x={0} y={0} width={16} height={38} rx={5} fill={DIARY_SPINE} />
      <path
        transform="translate(64 19) scale(0.9)"
        d="M0 6 C-10 -2 -10 -10 -4 -10 C-1 -10 0 -7 0 -6 C0 -7 1 -10 4 -10 C10 -10 10 -2 0 6 Z"
        fill={palette.paper}
        opacity={0.85}
      />
      <rect x={100} y={12} width={16} height={18} rx={3} fill={KEY_GOLD} />
      <circle cx={108} cy={20} r={3} fill={palette.ink} opacity={0.6} />
      {/* 钥匙 */}
      <g transform="translate(122 34) rotate(20)">
        <circle cx={0} cy={0} r={6} fill="none" stroke={KEY_GOLD} strokeWidth={3} />
        <path d="M6 0 h16 M18 0 v5 M22 0 v4" stroke={KEY_GOLD} strokeWidth={3} strokeLinecap="round" />
      </g>
    </g>
  );
}

/** 手机：平放着；有没看过的搭话时屏幕亮着、一闪一闪。 */
export function PhoneArt({ glowing, still }: { glowing: boolean; still: boolean }) {
  return (
    <g transform="rotate(-14 34 56)">
      <rect x={3} y={6} width={68} height={116} rx={12} fill={PHONE_BODY} opacity={0.25} />
      <rect width={68} height={116} rx={12} fill={PHONE_BODY} />
      <rect x={6} y={10} width={56} height={96} rx={6} fill={glowing ? SCREEN_GLOW : PHONE_BODY} stroke={SCREEN_GLOW} strokeOpacity={0.25}>
        {glowing && !still && <animate attributeName="opacity" values="1;0.55;1" dur="1.6s" repeatCount="indefinite" />}
      </rect>
      {glowing && (
        <g>
          <rect x={14} y={30} width={40} height={14} rx={7} fill={ENVELOPE} />
          <rect x={14} y={50} width={30} height={14} rx={7} fill={ENVELOPE} opacity={0.8} />
        </g>
      )}
      <rect x={26} y={4} width={16} height={3} rx={1.5} fill={SCREEN_GLOW} opacity={0.3} />
    </g>
  );
}

/** 抽屉柜：两层抽屉，上面那层露出一截缎带（礼物和券都收在这里）。 */
export function DrawerArt({ palette, w, h }: { palette: RoomPalette; w: number; h: number }) {
  const half = (h - 18) / 2;
  return (
    <g>
      <rect width={w} height={h} rx={6} fill={palette.wood} />
      {[0, 1].map((i) => (
        <g key={i} transform={`translate(8 ${8 + i * (half + 4)})`}>
          <rect width={w - 16} height={half} rx={4} fill={palette.woodLight} />
          <rect x={(w - 16) / 2 - 16} y={half / 2 - 4} width={32} height={8} rx={4} fill={palette.metal} />
        </g>
      ))}
      <path d={`M${w * 0.62} 10 q10 -18 22 -4 q-6 10 -22 4 Z`} fill={RIBBON} />
      <path d={`M${w * 0.62} 10 q-14 -16 -24 -2 q8 8 24 2 Z`} fill={RIBBON} opacity={0.85} />
    </g>
  );
}

/** 礼物盒（画好的屋子里代替抽屉柜）：系着缎带的盒子，礼物和券都收在里面。 */
export function GiftBoxArt({ palette }: { palette: RoomPalette }) {
  return (
    <g>
      <ellipse cx={60} cy={108} rx={56} ry={8} fill={palette.ink} opacity={0.18} />
      <rect x={6} y={40} width={108} height={68} rx={8} fill={palette.paper} />
      <rect x={0} y={26} width={120} height={22} rx={6} fill={palette.paper} stroke={palette.trim} strokeWidth={2} />
      <rect x={52} y={26} width={16} height={82} fill={RIBBON} />
      <path d="M60 26 q-34 -34 -40 -6 q4 12 40 6 Z" fill={RIBBON} />
      <path d="M60 26 q34 -34 40 -6 q-4 12 -40 6 Z" fill={RIBBON} />
      <circle cx={60} cy={24} r={7} fill={RIBBON} />
    </g>
  );
}

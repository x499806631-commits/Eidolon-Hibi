// 地图的画（GLOSSARY.md「地图」）：一张手绘感的东京——左上是住宅区，中间一圈山手线围着事务所，右边一条隅田川
// 流进右下角的东京湾。同家里的屋子一样是内容不是界面：颜色写在这里，不走主题变量，也不跟深浅色变；
// 只跟一天里的时候变（傍晚偏暖，夜里暗下来、窗户亮灯）。每一处画成一个小建筑，下面一块写名字的牌子。

import type { TimeOfDay } from "@/lib/home";
import { MAP_HEIGHT, MAP_WIDTH, PLACE_AT, type Place } from "@/lib/cityMap";

const W = MAP_WIDTH;
const H = MAP_HEIGHT;

const C = {
  land: "#f3ead8",
  landShade: "#e9dcc2",
  block: "#ece0c8",
  road: "#fffaf0",
  roadEdge: "#dccdb0",
  rail: "#8a7f74",
  railLoop: "#7fbf8e",
  water: "#a9d3e6",
  waterDeep: "#8ec3dc",
  wave: "#d8edf5",
  green: "#b8d9a0",
  greenDark: "#93c07a",
  tree: "#7fae66",
  sand: "#f2dcae",
  roofRed: "#e07a6a",
  roofBlue: "#7a9cc8",
  roofGreen: "#7fb08a",
  wall: "#fffaf2",
  wallWarm: "#fbe9d2",
  ink: "#5b4636",
  glass: "#cfe3f2",
  lit: "#ffd977",
  accent: "#f08fa0",
  gold: "#e3b552",
  tower: "#e4573d",
  label: "#fffdf8",
};

function at(place: Place): [number, number] {
  const [x, y] = PLACE_AT[place];
  return [x * W, y * H];
}

/** 地图本身：地面、水、绿地、铁路、道路和每一处的小建筑。名字的牌子由 `labels` 给。 */
export function MapArt({
  time,
  labels,
  highlight,
}: {
  time: TimeOfDay;
  labels: Record<Place, string>;
  /** 约会模式：能约的地方画得醒目些，平时的地方淡一点。 */
  highlight: "everyday" | "dates";
}) {
  const isNight = time === "night";
  const glow = isNight ? C.lit : C.glass;
  const dim = (place: Place) => (highlight === "dates" && EVERYDAY.has(place) ? 0.45 : 1);
  return (
    <g>
      <rect width={W} height={H} fill={C.land} />
      <Blocks />
      <Water />
      <Parks />
      <Roads />
      <Rail />
      {PLACES.map(([place, Draw]) => {
        const [x, y] = at(place);
        return (
          <g key={place} opacity={dim(place)} transform={`translate(${x} ${y})`}>
            <Draw glow={glow} />
            <Label text={labels[place]} />
          </g>
        );
      })}
      <TimeTint time={time} />
    </g>
  );
}

const EVERYDAY = new Set<Place>(["office", "lesson", "school", "studio", "venue", "home", "town", "away"]);

/** 底下的街区：一格一格浅色的块，看着像城市。 */
function Blocks() {
  const blocks: React.ReactNode[] = [];
  for (let gx = 0; gx < 16; gx++) {
    for (let gy = 0; gy < 10; gy++) {
      const x = gx * 100 + 14;
      const y = gy * 100 + 14;
      if ((gx * 7 + gy * 3) % 5 === 0) continue;
      blocks.push(<rect key={`${gx}-${gy}`} x={x} y={y} width={72} height={72} rx={10} fill={C.block} />);
    }
  }
  return <g>{blocks}</g>;
}

/** 隅田川从上往下流进右下角的东京湾。 */
function Water() {
  return (
    <g>
      <path
        d={`M1110 0 C1090 140 1150 220 1120 330 C1090 440 1140 520 1110 640 C1090 720 1120 760 1100 800`}
        stroke={C.water}
        strokeWidth={46}
        fill="none"
        strokeLinecap="round"
      />
      <path
        d={`M860 ${H} C900 900 1000 820 1080 790 C1200 740 1320 690 1450 650 C1520 630 1570 600 ${W} 590 L${W} ${H} Z`}
        fill={C.water}
      />
      <path
        d={`M1000 ${H} C1060 930 1180 880 1300 840 C1420 800 1520 780 ${W} 770 L${W} ${H} Z`}
        fill={C.waterDeep}
      />
      {[
        [1180, 900],
        [1320, 800],
        [1460, 720],
        [1500, 940],
        [1060, 960],
      ].map(([x, y]) => (
        <path
          key={`${x}-${y}`}
          d={`M${x} ${y} q12 -10 24 0 q12 10 24 0`}
          stroke={C.wave}
          strokeWidth={4}
          fill="none"
          strokeLinecap="round"
        />
      ))}
      <path
        d={`M1360 ${H} C1380 940 1430 900 1500 880 C1550 868 1580 860 ${W} 858 L${W} ${H} Z`}
        fill={C.sand}
      />
    </g>
  );
}

/** 代代木那样的大公园在左边，上野的绿地在上面。 */
function Parks() {
  return (
    <g>
      <ellipse cx={180} cy={510} rx={150} ry={120} fill={C.green} />
      <ellipse cx={930} cy={160} rx={140} ry={95} fill={C.green} />
      {[
        [110, 450],
        [160, 600],
        [250, 470],
        [90, 560],
        [860, 110],
        [1010, 200],
        [880, 230],
      ].map(([x, y]) => (
        <g key={`${x}-${y}`}>
          <circle cx={x} cy={y} r={20} fill={C.greenDark} />
          <circle cx={x + 14} cy={y - 8} r={14} fill={C.tree} />
        </g>
      ))}
    </g>
  );
}

/** 几条大路。 */
function Roads() {
  const roads = [
    `M0 300 C300 290 500 320 800 300 C1000 290 1100 300 ${W} 280`,
    `M0 640 C300 650 600 620 900 640 C1000 650 1060 660 1100 650`,
    `M420 0 C430 300 410 600 440 ${H}`,
    `M820 0 C800 260 840 560 800 ${H}`,
    `M1100 420 C1250 420 1400 400 ${W} 410`,
  ];
  return (
    <g fill="none" strokeLinecap="round">
      {roads.map((d) => (
        <path key={d} d={d} stroke={C.roadEdge} strokeWidth={30} />
      ))}
      {roads.map((d) => (
        <path key={`${d}-top`} d={d} stroke={C.road} strokeWidth={22} />
      ))}
    </g>
  );
}

/** 山手线：一圈绿色的环线，枕木一段一段。 */
function Rail() {
  return (
    <g fill="none">
      <ellipse cx={720} cy={420} rx={430} ry={280} stroke={C.rail} strokeWidth={10} />
      <ellipse cx={720} cy={420} rx={430} ry={280} stroke={C.railLoop} strokeWidth={6} strokeDasharray="18 12" />
    </g>
  );
}

/** 傍晚一层暖色，夜里一层深蓝。 */
function TimeTint({ time }: { time: TimeOfDay }) {
  if (time === "evening") return <rect width={W} height={H} fill="#ff9a5a" opacity={0.12} pointerEvents="none" />;
  if (time === "night") return <rect width={W} height={H} fill="#1d2747" opacity={0.38} pointerEvents="none" />;
  if (time === "morning") return <rect width={W} height={H} fill="#fff4d6" opacity={0.15} pointerEvents="none" />;
  return null;
}

/** 名字的牌子：圆角白底，在建筑下面。 */
function Label({ text }: { text: string }) {
  const width = Math.max(64, text.length * 26 + 28);
  return (
    <g transform="translate(0 62)">
      <rect x={-width / 2} y={-20} width={width} height={40} rx={20} fill={C.label} stroke={C.roadEdge} strokeWidth={2} />
      <text textAnchor="middle" dominantBaseline="central" fontSize={24} fontWeight={600} fill={C.ink}>
        {text}
      </text>
    </g>
  );
}

type Draw = (props: { glow: string }) => React.ReactNode;

function House({ x, y, roof, glow }: { x: number; y: number; roof: string; glow: string }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x={-22} y={-14} width={44} height={36} rx={4} fill={C.wall} stroke={C.ink} strokeWidth={2.5} />
      <path d="M-28 -12 L0 -36 L28 -12 Z" fill={roof} stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
      <rect x={-12} y={-4} width={12} height={12} rx={2} fill={glow} />
      <rect x={4} y={4} width={10} height={18} rx={2} fill={C.wallWarm} stroke={C.ink} strokeWidth={2} />
    </g>
  );
}

/** 她们住的地方：几栋小房子。 */
const Home: Draw = ({ glow }) => (
  <g>
    <House x={-40} y={0} roof={C.roofRed} glow={glow} />
    <House x={30} y={-14} roof={C.roofBlue} glow={glow} />
    <House x={10} y={22} roof={C.roofGreen} glow={glow} />
  </g>
);

function Building({
  width,
  height,
  color,
  glow,
  rows = 3,
}: {
  width: number;
  height: number;
  color: string;
  glow: string;
  rows?: number;
}) {
  const cols = Math.max(2, Math.floor(width / 22));
  return (
    <g>
      <rect x={-width / 2} y={-height + 30} width={width} height={height} rx={6} fill={color} stroke={C.ink} strokeWidth={2.5} />
      {Array.from({ length: rows * cols }, (_, i) => {
        const r = Math.floor(i / cols);
        const c = i % cols;
        const gap = width / cols;
        return (
          <rect
            key={i}
            x={-width / 2 + gap * c + gap / 2 - 6}
            y={-height + 42 + r * 20}
            width={12}
            height={11}
            rx={2}
            fill={glow}
          />
        );
      })}
    </g>
  );
}

/** 283 事务所：中间那栋，楼顶一颗星。 */
const Office: Draw = ({ glow }) => (
  <g>
    <Building width={92} height={92} color={C.wallWarm} glow={glow} />
    <path d="M0 -84 l7 14 15 2 -11 10 3 15 -14 -8 -14 8 3 -15 -11 -10 15 -2 z" fill={C.gold} stroke={C.ink} strokeWidth={2} />
    <text y={22} textAnchor="middle" fontSize={18} fontWeight={700} fill={C.ink}>
      283
    </text>
  </g>
);

const Lesson: Draw = ({ glow }) => (
  <g>
    <Building width={74} height={64} color="#f5dfe6" glow={glow} rows={2} />
    <path d="M-10 -46 v-18 a6 6 0 1 0 6 0" stroke={C.ink} strokeWidth={3} fill="none" strokeLinecap="round" />
  </g>
);

const School: Draw = ({ glow }) => (
  <g>
    <Building width={120} height={62} color={C.wall} glow={glow} rows={2} />
    <rect x={-14} y={-56} width={28} height={30} fill={C.wall} stroke={C.ink} strokeWidth={2.5} />
    <circle cx={0} cy={-42} r={9} fill={C.wall} stroke={C.ink} strokeWidth={2} />
  </g>
);

const Studio: Draw = ({ glow }) => (
  <g>
    <Building width={96} height={74} color="#dfe6f2" glow={glow} rows={2} />
    <circle cx={0} cy={-58} r={16} fill={C.roofBlue} stroke={C.ink} strokeWidth={2.5} />
    <path d="M0 -74 v-18" stroke={C.ink} strokeWidth={3} />
  </g>
);

/** Live 会场：圆顶的场馆，亮着灯牌。 */
const Venue: Draw = () => (
  <g>
    <path d="M-70 30 L-70 -6 Q0 -70 70 -6 L70 30 Z" fill="#efe3f7" stroke={C.ink} strokeWidth={2.5} />
    <path d="M-40 -28 Q0 -54 40 -28" stroke={C.accent} strokeWidth={6} fill="none" strokeLinecap="round" />
    <rect x={-22} y={4} width={44} height={26} rx={4} fill={C.lit} stroke={C.ink} strokeWidth={2} />
  </g>
);

/** 街上：车站和一块小广场。 */
const Town: Draw = ({ glow }) => (
  <g>
    <rect x={-56} y={-10} width={112} height={40} rx={8} fill="#e6eef0" stroke={C.ink} strokeWidth={2.5} />
    <path d="M-62 -10 L0 -40 L62 -10 Z" fill={C.railLoop} stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
    <rect x={-40} y={0} width={18} height={14} rx={2} fill={glow} />
    <rect x={22} y={0} width={18} height={14} rx={2} fill={glow} />
    <circle cx={0} cy={-18} r={8} fill={C.wall} stroke={C.ink} strokeWidth={2} />
  </g>
);

/** 外地：一张小卡片，上面一条新干线。 */
const Away: Draw = () => (
  <g>
    <rect x={-92} y={-50} width={184} height={88} rx={14} fill={C.wall} stroke={C.ink} strokeWidth={2.5} />
    <path d="M-70 10 C-30 -36 30 -40 70 -10" stroke={C.roofBlue} strokeWidth={5} fill="none" strokeDasharray="10 8" />
    <path d="M-50 18 h70 a20 14 0 0 0 20 -14 h-70 z" fill="#f4f6fb" stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
    <path d="M-44 10 h56" stroke={C.roofBlue} strokeWidth={3} />
  </g>
);

const Aquarium: Draw = () => (
  <g>
    <path d="M-60 30 L-60 -10 Q0 -60 60 -10 L60 30 Z" fill="#d6ecf6" stroke={C.ink} strokeWidth={2.5} />
    <path d="M-22 -6 q22 -20 44 0 q-22 20 -44 0 z M22 -6 l14 -10 v20 z" fill={C.roofBlue} stroke={C.ink} strokeWidth={2} strokeLinejoin="round" />
    <circle cx={-30} cy={-24} r={5} fill="none" stroke={C.wave} strokeWidth={3} />
  </g>
);

/** 游乐园：摩天轮。 */
const Amusement: Draw = () => (
  <g>
    <circle cx={0} cy={-18} r={46} fill="none" stroke={C.ink} strokeWidth={3} />
    {Array.from({ length: 8 }, (_, i) => {
      const a = (i * Math.PI) / 4;
      const x = Math.cos(a) * 46;
      const y = -18 + Math.sin(a) * 46;
      return (
        <g key={i}>
          <path d={`M0 -18 L${x} ${y}`} stroke={C.ink} strokeWidth={1.5} />
          <circle cx={x} cy={y} r={8} fill={[C.accent, C.gold, C.roofBlue, C.roofGreen][i % 4]} stroke={C.ink} strokeWidth={2} />
        </g>
      );
    })}
    <path d="M-24 30 L0 -18 L24 30" stroke={C.ink} strokeWidth={3.5} fill="none" />
  </g>
);

const Zoo: Draw = () => (
  <g>
    <circle cx={0} cy={-4} r={30} fill={C.wall} stroke={C.ink} strokeWidth={2.5} />
    <circle cx={-24} cy={-28} r={12} fill={C.ink} />
    <circle cx={24} cy={-28} r={12} fill={C.ink} />
    <ellipse cx={-11} cy={-6} rx={8} ry={10} fill={C.ink} />
    <ellipse cx={11} cy={-6} rx={8} ry={10} fill={C.ink} />
    <circle cx={-10} cy={-6} r={3} fill={C.wall} />
    <circle cx={12} cy={-6} r={3} fill={C.wall} />
    <ellipse cx={0} cy={8} rx={6} ry={4} fill={C.ink} />
  </g>
);

/** 东京塔：红白的铁塔。 */
const Tower: Draw = () => (
  <g>
    <path d="M-38 34 L-6 -96 L6 -96 L38 34 Z" fill={C.tower} stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
    <path d="M-30 4 h60 M-20 -36 h40 M-12 -66 h24" stroke={C.wall} strokeWidth={6} />
    <path d="M0 -96 v-24" stroke={C.ink} strokeWidth={3} />
  </g>
);

/** 海边：遮阳伞和沙滩。 */
const Sea: Draw = () => (
  <g>
    <path d="M0 30 V-24" stroke={C.ink} strokeWidth={3} />
    <path d="M-44 -18 Q0 -64 44 -18 Z" fill={C.accent} stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
    <path d="M-14 -18 Q0 -60 14 -18" fill={C.wall} />
    <circle cx={30} cy={22} r={10} fill={C.gold} stroke={C.ink} strokeWidth={2} />
  </g>
);

/** 浅草：红色的雷门和灯笼。 */
const Asakusa: Draw = () => (
  <g>
    <path d="M-58 -30 Q0 -50 58 -30 L50 -18 L-50 -18 Z" fill={C.ink} />
    <rect x={-48} y={-18} width={14} height={48} fill={C.tower} stroke={C.ink} strokeWidth={2} />
    <rect x={34} y={-18} width={14} height={48} fill={C.tower} stroke={C.ink} strokeWidth={2} />
    <rect x={-48} y={-16} width={96} height={12} fill={C.tower} stroke={C.ink} strokeWidth={2} />
    <ellipse cx={0} cy={10} rx={16} ry={20} fill={C.tower} stroke={C.ink} strokeWidth={2} />
    <path d="M-10 4 h20 M-10 16 h20" stroke={C.ink} strokeWidth={2} />
  </g>
);

const Cinema: Draw = ({ glow }) => (
  <g>
    <rect x={-50} y={-30} width={100} height={60} rx={6} fill="#3f3550" stroke={C.ink} strokeWidth={2.5} />
    <rect x={-40} y={-20} width={80} height={28} rx={4} fill={glow} />
    {[-30, -15, 0, 15, 30].map((x) => (
      <circle key={x} cx={x} cy={20} r={4} fill={C.lit} />
    ))}
  </g>
);

const Cafe: Draw = ({ glow }) => (
  <g>
    <rect x={-40} y={-16} width={80} height={46} rx={6} fill={C.wallWarm} stroke={C.ink} strokeWidth={2.5} />
    <path d="M-48 -16 h96 l-8 -18 h-80 z" fill={C.roofGreen} stroke={C.ink} strokeWidth={2.5} strokeLinejoin="round" />
    <rect x={-30} y={-6} width={26} height={20} rx={3} fill={glow} />
    <path d="M10 -2 h18 v14 a9 9 0 0 1 -18 0 z M28 2 a5 5 0 0 1 0 8" fill={C.wall} stroke={C.ink} strokeWidth={2} />
  </g>
);

const Park: Draw = () => (
  <g>
    <circle cx={-18} cy={-14} r={26} fill={C.tree} stroke={C.ink} strokeWidth={2.5} />
    <circle cx={18} cy={-22} r={22} fill={C.greenDark} stroke={C.ink} strokeWidth={2.5} />
    <path d="M-18 12 v18 M18 0 v30" stroke={C.ink} strokeWidth={3} />
    <rect x={-34} y={20} width={68} height={8} rx={3} fill={C.wallWarm} stroke={C.ink} strokeWidth={2} />
  </g>
);

/** 商店街：几家挂着彩色遮阳篷的小店。 */
const Shopping: Draw = ({ glow }) => (
  <g>
    {[-44, 0, 44].map((x, i) => (
      <g key={x} transform={`translate(${x} 0)`}>
        <rect x={-20} y={-20} width={40} height={50} fill={C.wall} stroke={C.ink} strokeWidth={2.5} />
        <path
          d="M-24 -20 h48 l-4 12 h-40 z"
          fill={[C.accent, C.gold, C.roofBlue][i]}
          stroke={C.ink}
          strokeWidth={2}
          strokeLinejoin="round"
        />
        <rect x={-12} y={0} width={24} height={16} rx={2} fill={glow} />
      </g>
    ))}
  </g>
);

const PLACES: [Place, Draw][] = [
  ["home", Home],
  ["school", School],
  ["zoo", Zoo],
  ["asakusa", Asakusa],
  ["park", Park],
  ["shopping", Shopping],
  ["town", Town],
  ["office", Office],
  ["lesson", Lesson],
  ["cafe", Cafe],
  ["cinema", Cinema],
  ["studio", Studio],
  ["tower", Tower],
  ["venue", Venue],
  ["amusement", Amusement],
  ["aquarium", Aquarium],
  ["sea", Sea],
  ["away", Away],
];

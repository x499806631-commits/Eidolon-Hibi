// 约会的背景（GLOSSARY.md「约会」）：没有生图时用代码画的插画，每个地方一张，每一站换上那一站的东西。
// 同家、地图一样是内容不是界面：颜色写在这里，不走主题变量；只跟一天里的时候变（户外傍晚偏暖、夜里暗下来）。
// 画面中间偏下留给立绘，东西多摆在两边和上面。

import type { ReactNode } from "react";

import type { TimeOfDay } from "@/lib/home";

export const ART_WIDTH = 1600;
export const ART_HEIGHT = 900;
const W = ART_WIDTH;
const H = ART_HEIGHT;

const C = {
  ink: "#5b4636",
  white: "#fffaf2",
  cream: "#f6ecdc",
  wood: "#c99a6b",
  woodDark: "#9c7049",
  red: "#e4573d",
  pink: "#f3a6b6",
  gold: "#e3b552",
  green: "#8fbf7a",
  greenDark: "#6fa35e",
  sky: "#bfe2f2",
  skyDeep: "#8ec3dc",
  water: "#6fb3d6",
  waterDeep: "#2f6f9a",
  glow: "#ffe7a3",
};

const OUTDOOR_SKY: Record<TimeOfDay, [string, string]> = {
  morning: ["#cfe9f5", "#fff4dc"],
  day: ["#9fd3ee", "#e3f4fb"],
  evening: ["#f6a86b", "#fbd9a8"],
  night: ["#1f2a4d", "#3d4a78"],
};

/** 一站的背景：地方 `spot`（null 是在街上随便逛），这一站 `stop`，现在的时段。 */
export function OutingArt({ spot, stop, time }: { spot: string | null; stop: string; time: TimeOfDay }) {
  const scene = SCENES[spot ?? "around"] ?? SCENES.around;
  const { indoor, draw } = scene(stop);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid slice" className="block size-full" aria-hidden>
      <defs>
        <linearGradient id="outing-sky" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor={OUTDOOR_SKY[time][0]} />
          <stop offset="1" stopColor={OUTDOOR_SKY[time][1]} />
        </linearGradient>
        <linearGradient id="outing-deep" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stopColor="#0d2a45" />
          <stop offset="1" stopColor="#1d4f78" />
        </linearGradient>
      </defs>
      {!indoor && <rect width={W} height={H} fill="url(#outing-sky)" />}
      {draw}
      {!indoor && time === "night" && <rect width={W} height={H} fill="#0b1030" opacity={0.25} />}
      {!indoor && time === "evening" && <rect width={W} height={H} fill="#ff8a3d" opacity={0.08} />}
    </svg>
  );
}

type Scene = (stop: string) => { indoor: boolean; draw: ReactNode };

/** 地面一条。 */
function Ground({ y = 720, color = "#d9c9a8" }: { y?: number; color?: string }) {
  return <rect y={y} width={W} height={H - y} fill={color} />;
}

/** 室内的墙和地板。 */
function Room({ wall, floor, line = 640 }: { wall: string; floor: string; line?: number }) {
  return (
    <>
      <rect width={W} height={line} fill={wall} />
      <rect y={line} width={W} height={H - line} fill={floor} />
      <rect y={line - 8} width={W} height={10} fill="#00000014" />
    </>
  );
}

function Tree({ x, y, s = 1 }: { x: number; y: number; s?: number }) {
  return (
    <g transform={`translate(${x} ${y}) scale(${s})`}>
      <rect x={-10} y={0} width={20} height={90} fill={C.woodDark} />
      <circle cx={0} cy={-10} r={70} fill={C.greenDark} />
      <circle cx={-40} cy={20} r={50} fill={C.green} />
      <circle cx={45} cy={15} r={55} fill={C.green} />
    </g>
  );
}

function Buildings({ base = 720, tint = "#cfd8e6" }: { base?: number; tint?: string }) {
  const blocks = [
    [0, 260, 180], [170, 200, 140], [300, 320, 160], [470, 240, 120], [1050, 280, 150],
    [1190, 220, 170], [1350, 300, 130], [1470, 250, 140],
  ];
  return (
    <g>
      {blocks.map(([x, h, w]) => (
        <g key={x}>
          <rect x={x} y={base - h} width={w} height={h} fill={tint} stroke="#00000010" />
          {Array.from({ length: Math.floor(h / 50) }, (_, r) => (
            <rect key={r} x={x + 20} y={base - h + 24 + r * 50} width={w - 40} height={14} fill="#ffffff66" />
          ))}
        </g>
      ))}
    </g>
  );
}

function Bench({ x, y }: { x: number; y: number }) {
  return (
    <g transform={`translate(${x} ${y})`}>
      <rect x={-90} y={-30} width={180} height={16} rx={4} fill={C.wood} />
      <rect x={-90} y={-60} width={180} height={14} rx={4} fill={C.wood} />
      <rect x={-80} y={-14} width={10} height={40} fill={C.woodDark} />
      <rect x={70} y={-14} width={10} height={40} fill={C.woodDark} />
    </g>
  );
}

function Lanterns({ y = 120 }: { y?: number }) {
  return (
    <g>
      <path d={`M0 ${y} Q${W / 2} ${y + 60} ${W} ${y}`} stroke={C.ink} strokeWidth={3} fill="none" />
      {Array.from({ length: 9 }, (_, i) => {
        const x = 90 + i * 180;
        const t = x / W;
        const cy = y + 4 * 60 * t * (1 - t) + 30;
        return <ellipse key={i} cx={x} cy={cy} rx={22} ry={30} fill={i % 2 ? C.red : C.glow} stroke={C.ink} strokeWidth={2} />;
      })}
    </g>
  );
}

const SCENES: Record<string, Scene> = {
  aquarium: (stop) => {
    if (stop === "jellyfish")
      return {
        indoor: true,
        draw: (
          <>
            <rect width={W} height={H} fill="url(#outing-deep)" />
            {Array.from({ length: 14 }, (_, i) => {
              const x = (i * 137) % W;
              const y = 80 + ((i * 211) % 520);
              const r = 26 + (i % 4) * 10;
              return (
                <g key={i} opacity={0.85}>
                  <path d={`M${x - r} ${y} a${r} ${r * 0.8} 0 0 1 ${2 * r} 0 z`} fill="#c9e9ff" />
                  {[-0.6, -0.2, 0.2, 0.6].map((k) => (
                    <path key={k} d={`M${x + k * r} ${y} q${8} ${r} 0 ${r * 1.8}`} stroke="#c9e9ff" strokeWidth={2} fill="none" />
                  ))}
                </g>
              );
            })}
            <rect y={760} width={W} height={140} fill="#0a1d33" />
          </>
        ),
      };
    if (stop === "penguins")
      return {
        indoor: true,
        draw: (
          <>
            <Room wall="#d7eef7" floor="#bcd9e6" line={700} />
            <rect x={80} y={120} width={W - 160} height={520} rx={20} fill={C.water} stroke={C.white} strokeWidth={10} />
            <path d={`M80 520 Q500 440 900 520 T${W - 80} 500 V640 H80 Z`} fill="#e8eef0" />
            {[300, 520, 1180, 1320].map((x, i) => (
              <g key={x} transform={`translate(${x} ${470 + (i % 2) * 20})`}>
                <ellipse cx={0} cy={0} rx={26} ry={42} fill="#2b2b33" />
                <ellipse cx={0} cy={8} rx={17} ry={30} fill={C.white} />
                <path d="M-6 -30 l6 8 6 -8 z" fill={C.gold} />
              </g>
            ))}
          </>
        ),
      };
    if (stop === "shop")
      return { indoor: true, draw: <Shop wall="#f3e6f0" items={["#9ec9e8", "#f3a6b6", "#ffe08a", "#b9e3c6"]} /> };
    return {
      indoor: true,
      draw: (
        <>
          <Room wall="#cfe3ef" floor="#a9c4d3" />
          <path d="M200 160 Q800 40 1400 160 Q1200 230 800 210 Q400 230 200 160 Z" fill={C.white} opacity={0.9} />
          {Array.from({ length: 12 }, (_, i) => (
            <rect key={i} x={260 + i * 92} y={176 + Math.abs(6 - i) * 4} width={10} height={40} fill={C.white} opacity={0.9} />
          ))}
          <rect x={60} y={260} width={300} height={340} rx={12} fill="#7fb8d8" />
          <rect x={1240} y={260} width={300} height={340} rx={12} fill="#7fb8d8" />
        </>
      ),
    };
  },
  amusement: (stop) => {
    if (stop === "wheel")
      return {
        indoor: true,
        draw: (
          <>
            <rect width={W} height={H} fill="url(#outing-sky)" />
            <Buildings base={900} tint="#e7c7d9" />
            <rect x={0} y={0} width={W} height={H} fill="none" stroke="#e8d3a6" strokeWidth={120} />
            <rect x={0} y={700} width={W} height={200} fill="#e8d3a6" />
          </>
        ),
      };
    return {
      indoor: false,
      draw: (
        <>
          <g transform="translate(1250 360)">
            <circle r={260} fill="none" stroke={C.white} strokeWidth={10} />
            {Array.from({ length: 12 }, (_, i) => {
              const a = (i * Math.PI) / 6;
              return (
                <g key={i}>
                  <path d={`M0 0 L${Math.cos(a) * 260} ${Math.sin(a) * 260}`} stroke={C.white} strokeWidth={4} />
                  <circle cx={Math.cos(a) * 260} cy={Math.sin(a) * 260} r={22} fill={[C.pink, C.gold, C.sky, C.green][i % 4]} />
                </g>
              );
            })}
          </g>
          {stop === "coaster" && (
            <path d="M0 420 Q200 120 420 380 T860 300 T1100 460" stroke={C.red} strokeWidth={16} fill="none" />
          )}
          <Ground y={700} color="#e9dcc0" />
          {stop === "carousel" ? (
            <g transform="translate(320 560)">
              <path d="M-200 -120 L0 -260 L200 -120 Z" fill={C.pink} stroke={C.white} strokeWidth={6} />
              <rect x={-200} y={-120} width={400} height={150} fill="#fff1d6" />
              {[-140, -50, 40, 130].map((x) => (
                <rect key={x} x={x} y={-110} width={8} height={140} fill={C.gold} />
              ))}
            </g>
          ) : (
            <g transform="translate(300 700)">
              <path d="M-220 0 V-300 Q0 -420 220 -300 V0" fill="none" stroke={C.pink} strokeWidth={40} />
              {[-160, -60, 60, 160].map((x, i) => (
                <circle key={x} cx={x} cy={-330 - (i % 2) * 40} r={24} fill={[C.gold, C.sky, C.green, C.red][i]} />
              ))}
            </g>
          )}
        </>
      ),
    };
  },
  zoo: (stop) => {
    if (stop === "pandas")
      return {
        indoor: true,
        draw: (
          <>
            <Room wall="#e2efd9" floor="#bcd3a8" />
            <rect x={60} y={120} width={W - 120} height={480} rx={20} fill="#c7e0b0" stroke={C.white} strokeWidth={10} />
            {Array.from({ length: 10 }, (_, i) => (
              <rect key={i} x={120 + i * 145} y={140} width={14} height={440} fill="#7fae66" />
            ))}
            <g transform="translate(1250 470)">
              <ellipse rx={90} ry={70} fill={C.white} />
              <circle cx={-50} cy={-60} r={22} fill="#2b2b33" />
              <circle cx={50} cy={-60} r={22} fill="#2b2b33" />
              <ellipse cx={-28} cy={-12} rx={16} ry={20} fill="#2b2b33" />
              <ellipse cx={28} cy={-12} rx={16} ry={20} fill="#2b2b33" />
            </g>
          </>
        ),
      };
    return {
      indoor: false,
      draw: (
        <>
          {stop === "pond" ? (
            <>
              <Ground y={600} color={C.water} />
              <path d="M0 600 Q400 560 800 600 T1600 590" fill={C.water} />
              {[300, 1200].map((x) => (
                <g key={x} transform={`translate(${x} 660)`}>
                  <ellipse rx={70} ry={26} fill={C.white} />
                  <path d="M40 -10 q20 -60 -10 -70" stroke={C.white} strokeWidth={14} fill="none" />
                </g>
              ))}
            </>
          ) : (
            <>
              <Ground y={700} color="#d6c7a0" />
              <rect x={100} y={420} width={320} height={200} rx={10} fill="#a2c48a" stroke={C.ink} strokeWidth={4} />
              <text x={260} y={530} textAnchor="middle" fontSize={44} fill={C.white} fontWeight={700}>
                ZOO
              </text>
            </>
          )}
          <Tree x={150} y={600} />
          <Tree x={1420} y={580} s={1.2} />
        </>
      ),
    };
  },
  tower: (stop) => {
    if (stop === "deck" || stop === "night")
      return {
        indoor: true,
        draw: (
          <>
            <rect width={W} height={H} fill={stop === "night" ? "#141c3a" : "#a8d4ec"} />
            <Buildings base={720} tint={stop === "night" ? "#2a3460" : "#c3d3e3"} />
            {stop === "night" &&
              Array.from({ length: 80 }, (_, i) => (
                <circle key={i} cx={(i * 97) % W} cy={480 + ((i * 53) % 240)} r={3} fill={C.glow} />
              ))}
            {[0, 400, 800, 1200, 1600].map((x) => (
              <rect key={x} x={x - 12} y={0} width={24} height={H} fill="#3a3a48" />
            ))}
            <rect y={760} width={W} height={140} fill="#3a3a48" />
          </>
        ),
      };
    return {
      indoor: false,
      draw: (
        <>
          <path d="M700 900 L780 40 L820 40 L900 900 Z" fill={C.red} />
          {[700, 520, 340, 180].map((y) => (
            <rect key={y} x={730 + (900 - y) / 30} y={y} width={140 - (900 - y) / 15} height={24} fill={C.white} />
          ))}
          <Buildings />
          <Ground />
        </>
      ),
    };
  },
  sea: (stop) => ({
    indoor: false,
    draw: (
      <>
        {stop === "sunset" && <circle cx={800} cy={520} r={90} fill="#ffcf7a" />}
        <rect y={520} width={W} height={260} fill={C.water} />
        {[600, 650, 700].map((y) => (
          <path key={y} d={`M0 ${y} q100 -14 200 0 t200 0 t200 0 t200 0 t200 0 t200 0 t200 0 t200 0`} stroke={C.white} strokeWidth={4} fill="none" opacity={0.6} />
        ))}
        {stop === "station" ? (
          <>
            <rect y={720} width={W} height={180} fill="#cfc6b4" />
            <rect x={100} y={460} width={300} height={260} fill={C.cream} stroke={C.ink} strokeWidth={4} />
            <path d="M80 460 L250 380 L420 460 Z" fill={C.red} />
          </>
        ) : (
          <path d={`M0 760 Q800 700 ${W} 760 V${H} H0 Z`} fill="#f2dcae" />
        )}
      </>
    ),
  }),
  asakusa: (stop) => ({
    indoor: false,
    draw: (
      <>
        {stop === "temple" ? (
          <g>
            <path d="M300 300 L800 160 L1300 300 Z" fill="#3b3b44" />
            <rect x={380} y={300} width={840} height={360} fill={C.red} />
            <rect x={460} y={360} width={680} height={300} fill="#5a2b22" />
            <rect x={720} y={620} width={160} height={80} fill="#7a6a5a" />
          </g>
        ) : stop === "street" ? (
          <>
            {[0, 1, 2, 3].map((i) => (
              <g key={i}>
                <rect x={i * 140} y={300 + i * 30} width={120} height={420 - i * 30} fill={C.red} opacity={0.9} />
                <rect x={W - 120 - i * 140} y={300 + i * 30} width={120} height={420 - i * 30} fill={C.red} opacity={0.9} />
              </g>
            ))}
            <Lanterns y={160} />
          </>
        ) : (
          <g transform="translate(800 0)">
            <path d="M-520 230 Q0 150 520 230 L480 280 L-480 280 Z" fill="#3b3b44" />
            <rect x={-460} y={280} width={70} height={440} fill={C.red} />
            <rect x={390} y={280} width={70} height={440} fill={C.red} />
            <ellipse cx={0} cy={430} rx={120} ry={150} fill={C.red} stroke={C.ink} strokeWidth={6} />
            <text y={450} textAnchor="middle" fontSize={64} fill={C.ink} fontWeight={700}>
              雷門
            </text>
          </g>
        )}
        <Ground y={720} color="#cdbfa8" />
      </>
    ),
  }),
  cinema: (stop) => {
    if (stop === "seats")
      return {
        indoor: true,
        draw: (
          <>
            <rect width={W} height={H} fill="#1a1426" />
            <rect x={200} y={60} width={1200} height={420} rx={12} fill="#cfd8ff" opacity={0.85} />
            {[560, 660, 760].map((y, r) =>
              Array.from({ length: 12 }, (_, i) => (
                <rect key={`${r}-${i}`} x={60 + i * 128 + r * 20} y={y} width={100} height={70} rx={20} fill="#7a2e3f" />
              )),
            )}
          </>
        ),
      };
    if (stop === "after") return SCENES.around("stroll");
    return {
      indoor: true,
      draw: (
        <>
          <Room wall="#3e2f4f" floor="#5b3b4a" />
          {[120, 460, 1100, 1300].map((x, i) => (
            <rect key={x} x={x} y={120} width={180} height={260} rx={6} fill={[C.pink, C.sky, C.gold, C.green][i]} stroke={C.white} strokeWidth={6} />
          ))}
          <rect x={560} y={420} width={480} height={160} rx={12} fill="#7a2e3f" />
          <text x={800} y={520} textAnchor="middle" fontSize={56} fill={C.glow} fontWeight={700}>
            POPCORN
          </text>
        </>
      ),
    };
  },
  cafe: (stop) => {
    if (stop === "walk") return SCENES.around("stroll");
    return {
      indoor: true,
      draw: (
        <>
          <Room wall="#f1e3cf" floor="#c99a6b" />
          <rect x={980} y={100} width={500} height={420} rx={12} fill="#cfe7f2" stroke={C.white} strokeWidth={14} />
          <Tree x={1230} y={460} s={0.7} />
          <rect x={120} y={140} width={300} height={200} rx={8} fill="#3d3d3d" />
          <text x={270} y={250} textAnchor="middle" fontSize={40} fill={C.white}>
            {stop === "counter" ? "TODAY'S" : "CAFE"}
          </text>
          {stop === "window" && <rect x={300} y={660} width={1000} height={40} rx={10} fill={C.woodDark} />}
        </>
      ),
    };
  },
  park: (stop) => ({
    indoor: false,
    draw: (
      <>
        <Ground y={640} color="#a9d18e" />
        {stop === "pond" && <ellipse cx={800} cy={760} rx={600} ry={110} fill={C.water} />}
        {stop === "path" && <path d="M700 900 L780 640 L820 640 L900 900 Z" fill="#e6d2a8" />}
        <Tree x={180} y={560} s={1.3} />
        <Tree x={420} y={600} />
        <Tree x={1200} y={590} />
        <Tree x={1440} y={560} s={1.3} />
        {stop === "lawn" && <rect x={600} y={760} width={400} height={80} rx={6} fill="#f3a6b6" opacity={0.85} />}
        {stop === "pond" && <Bench x={1380} y={760} />}
      </>
    ),
  }),
  shopping: (stop) => {
    if (stop === "shop") return { indoor: true, draw: <Shop wall="#fdeef2" items={["#f3a6b6", "#ffe08a", "#cbb6ea", "#9ec9e8"]} /> };
    if (stop === "purikura")
      return {
        indoor: true,
        draw: (
          <>
            <rect width={W} height={H} fill="#ffd6e8" />
            {Array.from({ length: 30 }, (_, i) => (
              <circle key={i} cx={(i * 211) % W} cy={(i * 97) % H} r={10 + (i % 3) * 8} fill={C.white} opacity={0.6} />
            ))}
            <rect x={60} y={100} width={360} height={600} rx={20} fill="#fff4fa" stroke={C.pink} strokeWidth={10} />
          </>
        ),
      };
    return {
      indoor: false,
      draw: (
        <>
          {[0, 1, 2].map((i) => (
            <g key={i}>
              <rect x={i * 170} y={260 + i * 40} width={160} height={460 - i * 40} fill={[C.pink, C.cream, C.sky][i]} stroke="#00000010" />
              <rect x={W - 160 - i * 170} y={260 + i * 40} width={160} height={460 - i * 40} fill={[C.gold, C.pink, C.cream][i]} stroke="#00000010" />
              <path d={`M${i * 170} ${300 + i * 40} h160 l-10 30 h-140 z`} fill={C.red} opacity={0.8} />
              <path d={`M${W - 160 - i * 170} ${300 + i * 40} h160 l-10 30 h-140 z`} fill={C.greenDark} opacity={0.8} />
            </g>
          ))}
          <Ground y={720} color="#d8cbb4" />
        </>
      ),
    };
  },
  // 偶遇（GLOSSARY.md「偶遇」）：她平时在的地方，各一张。
  office: () => ({
    indoor: true,
    draw: (
      <>
        <Room wall="#f4efe6" floor="#b9a68c" />
        <rect x={100} y={140} width={420} height={260} rx={8} fill={C.white} stroke="#9aa3ad" strokeWidth={10} />
        {[0, 1, 2, 3].map((i) => (
          <rect key={i} x={140} y={180 + i * 50} width={200 + (i % 2) * 120} height={10} rx={5} fill="#9ec9e8" />
        ))}
        <rect x={1080} y={100} width={380} height={300} rx={10} fill="#cfe7f2" stroke={C.white} strokeWidth={14} />
        <rect x={60} y={600} width={480} height={90} rx={30} fill="#8fa8c8" />
        <rect x={60} y={540} width={480} height={80} rx={30} fill="#a6bcd8" />
        <rect x={1100} y={560} width={420} height={30} fill={C.woodDark} />
        <rect x={1140} y={500} width={120} height={60} fill={C.cream} />
        <rect x={1300} y={520} width={90} height={40} fill="#f3d27a" />
      </>
    ),
  }),
  lesson: () => ({
    indoor: true,
    draw: (
      <>
        <Room wall="#e9eef2" floor="#d2a679" line={600} />
        <rect x={40} y={80} width={W - 80} height={500} fill="#dbe9f2" stroke="#b5c3cf" strokeWidth={8} />
        {[1, 2, 3, 4, 5].map((i) => (
          <rect key={i} x={40 + i * ((W - 80) / 6)} y={80} width={6} height={500} fill="#b5c3cf" />
        ))}
        <rect x={40} y={440} width={W - 80} height={10} fill="#9aa3ad" />
        {Array.from({ length: 8 }, (_, i) => (
          <rect key={i} x={i * 200} y={640 + (i % 2) * 120} width={200} height={4} fill="#00000012" />
        ))}
        <rect x={1380} y={480} width={120} height={160} rx={10} fill="#3d3d45" />
        <circle cx={1440} cy={540} r={34} fill="#55555f" />
        <rect x={120} y={600} width={30} height={60} rx={8} fill="#9ec9e8" />
      </>
    ),
  }),
  school: () => ({
    indoor: false,
    draw: (
      <>
        <rect x={250} y={200} width={1100} height={480} fill="#efe6d6" stroke="#00000010" />
        {[0, 1, 2].map((r) =>
          Array.from({ length: 8 }, (_, i) => (
            <rect key={`${r}-${i}`} x={300 + i * 130} y={250 + r * 130} width={90} height={70} fill="#cfe7f2" />
          )),
        )}
        <circle cx={800} cy={170} r={50} fill={C.white} stroke="#9aa3ad" strokeWidth={8} />
        <Ground y={680} color="#d9c9a8" />
        <rect x={80} y={440} width={40} height={280} fill="#9aa3ad" />
        <rect x={1480} y={440} width={40} height={280} fill="#9aa3ad" />
        <Tree x={160} y={560} s={1.1} />
        <Tree x={1430} y={560} s={1.1} />
        {Array.from({ length: 12 }, (_, i) => (
          <circle key={i} cx={(i * 137) % W} cy={420 + ((i * 53) % 200)} r={8} fill={C.pink} opacity={0.8} />
        ))}
      </>
    ),
  }),
  studio: () => ({
    indoor: true,
    draw: (
      <>
        <rect width={W} height={H} fill="#2b2b33" />
        <path d="M420 60 H1180 V760 Q800 820 420 760 Z" fill="#f2f0ec" />
        {[200, 1400].map((x) => (
          <g key={x}>
            <rect x={x - 6} y={300} width={12} height={460} fill="#55555f" />
            <rect x={x - 90} y={180} width={180} height={140} rx={10} fill="#ececf2" stroke="#55555f" strokeWidth={8} />
            <path d={`M${x - 60} 760 L${x} 700 L${x + 60} 760`} stroke="#55555f" strokeWidth={8} fill="none" />
          </g>
        ))}
        <rect x={1260} y={640} width={140} height={90} rx={8} fill="#3d3d45" />
      </>
    ),
  }),
  venue: () => ({
    indoor: true,
    draw: (
      <>
        <Room wall="#3b3f52" floor="#2a2c38" line={620} />
        {[160, 520, 1100].map((x, i) => (
          <rect key={x} x={x} y={150 + (i % 2) * 40} width={260} height={360} rx={6} fill={[C.pink, C.gold, C.sky][i]} opacity={0.85} />
        ))}
        {[0, 1, 2].map((i) => (
          <rect key={i} x={1000 + i * 170} y={600} width={150} height={110} rx={8} fill="#1d1f29" stroke="#55596e" strokeWidth={6} />
        ))}
        <rect y={40} width={W} height={20} fill="#55596e" />
        {Array.from({ length: 8 }, (_, i) => (
          <circle key={i} cx={100 + i * 200} cy={70} r={18} fill={C.glow} opacity={0.9} />
        ))}
      </>
    ),
  }),
  town: () => SCENES.around("meet"),
  around: (stop) => ({
    indoor: false,
    draw: (
      <>
        <Buildings />
        <Ground />
        {stop === "meet" && (
          <g transform="translate(250 720)">
            <rect x={-10} y={-260} width={20} height={260} fill="#7a7a86" />
            <circle cx={0} cy={-290} r={46} fill={C.white} stroke="#7a7a86" strokeWidth={8} />
            <path d="M0 -290 v-26 M0 -290 h20" stroke={C.ink} strokeWidth={5} />
          </g>
        )}
        {stop === "rest" && <Bench x={1300} y={780} />}
        <Tree x={1450} y={600} s={0.9} />
      </>
    ),
  }),
};

function Shop({ wall, items }: { wall: string; items: string[] }) {
  return (
    <>
      <Room wall={wall} floor="#d9c4a8" />
      {[160, 360, 560].map((y) =>
        [0, 1].map((side) => (
          <g key={`${y}-${side}`}>
            <rect x={side ? 1100 : 80} y={y} width={420} height={14} fill={C.wood} />
            {Array.from({ length: 6 }, (_, i) => (
              <circle key={i} cx={(side ? 1130 : 110) + i * 66} cy={y - 26} r={24} fill={items[(i + y / 200) % items.length | 0]} />
            ))}
          </g>
        )),
      )}
    </>
  );
}

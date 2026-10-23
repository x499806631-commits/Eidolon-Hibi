// 地图（`/home/map`，GLOSSARY.md「地图」）：一张画出来的东京，看她们现在各在哪（「现在」），和约她去哪儿（「约会」）。
// 后端按她今天的行程说她在地图上的哪一处；这里管每一处画在哪、叫什么、约会时默认一起做什么。

import type { ScheduleNow } from "@/lib/schedule";
import { api, json } from "@/lib/api";

/** 平时的地方：事务所、练习室、学校、摄影棚、Live 会场、她家、街上，和地图角上的「外地」。 */
export type EverydayPlace = "office" | "lesson" | "school" | "studio" | "venue" | "home" | "town" | "away";

/** 能约她去的地方。 */
export type DateSpot =
  | "aquarium"
  | "amusement"
  | "zoo"
  | "tower"
  | "sea"
  | "asakusa"
  | "cinema"
  | "cafe"
  | "park"
  | "shopping";

export type Place = EverydayPlace | DateSpot;

export const DATE_SPOTS: readonly DateSpot[] = [
  "aquarium",
  "amusement",
  "zoo",
  "tower",
  "sea",
  "asakusa",
  "cinema",
  "cafe",
  "park",
  "shopping",
];

/** 一位偶像现在在哪：地图上的一处，和她行程里的这一段（同对话页页首那一行）。 */
export type IdolOnMap = { character_id: string; place: Place; trip: string | null } & NonNullable<ScheduleNow>;

/** 约好的一次约会：和谁、哪天（YYYY-MM-DD）、做什么；说的是地图上的哪一处，不是就 null。 */
export type DateOnMap = { character_id: string; day: string; what: string; spot: DateSpot | null };

export type CityMap = {
  /** 行程开着（部署者和用户都开着）：关着时地图上没有人，只有约会。 */
  schedules: boolean;
  idols: IdolOnMap[];
  dates: DateOnMap[];
};

export async function getCityMap(): Promise<CityMap> {
  return json(await api("/api/map"));
}

/** 地图画布的大小（SVG 的 viewBox）。 */
export const MAP_WIDTH = 1600;
export const MAP_HEIGHT = 1000;

/** 每一处在地图上的位置：横、竖各占整张图的几分之几（它的中心）。 */
export const PLACE_AT: Record<Place, [number, number]> = {
  home: [0.13, 0.2],
  school: [0.33, 0.14],
  zoo: [0.58, 0.16],
  asakusa: [0.8, 0.13],
  park: [0.12, 0.5],
  shopping: [0.27, 0.42],
  office: [0.47, 0.42],
  lesson: [0.6, 0.38],
  cafe: [0.37, 0.58],
  cinema: [0.25, 0.68],
  studio: [0.48, 0.7],
  tower: [0.64, 0.6],
  venue: [0.79, 0.45],
  amusement: [0.8, 0.72],
  aquarium: [0.62, 0.86],
  sea: [0.9, 0.9],
  town: [0.38, 0.3],
  away: [0.09, 0.86],
};

const PER_ROW = 4;

/** 同一处有几个人时，第 `i` 个（共 `n` 个）的头像离那一处的中心挪多少（像素，画布坐标）：立在建筑上面，
 * 一排最多 `PER_ROW` 个，多了往上再排一排。 */
export function fanned(i: number, n: number, step = 70, lift = 96): [number, number] {
  const row = Math.floor(i / PER_ROW);
  const inRow = Math.min(PER_ROW, n - row * PER_ROW);
  const col = i % PER_ROW;
  return [Math.round((col - (inRow - 1) / 2) * step), -lift - row * step];
}

/** 按地方分好的人，地方按 `PLACE_AT` 的次序。 */
export function byPlace(idols: IdolOnMap[]): Map<Place, IdolOnMap[]> {
  const grouped = new Map<Place, IdolOnMap[]>();
  for (const idol of idols) {
    const here = grouped.get(idol.place);
    if (here) here.push(idol);
    else grouped.set(idol.place, [idol]);
  }
  return grouped;
}

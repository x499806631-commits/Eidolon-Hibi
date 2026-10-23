// 制作人的家（`/home`，GLOSSARY.md「制作人的家」）：用户自己的一间屋子，画在浏览器里。后端只记选了哪间屋子、
// 墙上挂哪张照片，再告诉窗外现在是什么天；屋里每样东西点了通到应用里对应的地方。什么时候算早上、傍晚，
// 跟着浏览器的时钟走。

import { api, json } from "@/lib/api";

/** 一室公寓，或大房子。 */
export type Room = "apartment" | "mansion";
export const ROOMS: readonly Room[] = ["apartment", "mansion"];

/** 窗外的天：部署者没给天气地点时是 null，当晴天画。 */
export type Sky = "clear" | "cloudy" | "fog" | "rain" | "snow" | "storm";

/** 一样东西在画里的位置：横、竖各占整张画的几分之几（它的中心）。 */
export type Spot = [number, number];
export type Layout = Partial<Record<HomeThing, Spot>>;

export type HomeState = {
  room: Room;
  /** 墙上相框里挂的照片；没挂是 null。 */
  framed_photo: string | null;
  sky: Sky | null;
  /** 这间屋子的画画过几次；没画过是 null（那就用代码画的屋子）。 */
  background: number | null;
  /** 部署者配了生图，能画。 */
  can_draw: boolean;
  /** 用户在这间屋子的画里把东西摆在哪；没摆过的用 `PICTURE_LAYOUT`。 */
  layout: Layout;
};

export type AlbumPhoto = {
  id: string;
  character_id: string;
  sent_at: string;
};

export async function getHome(): Promise<HomeState> {
  return json(await api("/api/home"));
}

/** 换屋子、换挂的照片（`framed_photo: null` 是取下来），或者把东西摆到这间屋子的画里别处。 */
export async function changeHome(change: { room?: Room; framed_photo?: string | null; layout?: Layout }): Promise<HomeState> {
  return json(
    await api("/api/home", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(change),
    }),
  );
}

/** 用部署者的生图把这间屋子画一张（画过的重画），画好了给回新的家。要等十几秒。 */
export async function drawRoom(): Promise<HomeState> {
  return json(await api("/api/home/background", { method: "POST" }));
}

/** 这间屋子的画；每画一次一个新地址。 */
export function roomPictureUrl(room: Room, drawn: number): string {
  return `/api/home/background/${room}?v=${drawn}`;
}

/** 在画好的屋子里，没摆过的东西先放在哪：沿着墙摆一圈，用户再拖到画里合适的地方。 */
export const PICTURE_LAYOUT: Record<HomeThing, Spot> = {
  mailbox: [0.1, 0.42],
  window: [0.34, 0.22],
  calendar: [0.55, 0.2],
  frame: [0.7, 0.22],
  shelf: [0.86, 0.3],
  diary: [0.78, 0.62],
  drawer: [0.88, 0.8],
  phone: [0.5, 0.82],
};

/** 东西摆在哪：用户摆过的，没摆过的放在 `PICTURE_LAYOUT`。 */
export function spotOf(layout: Layout, thing: HomeThing): Spot {
  return layout[thing] ?? PICTURE_LAYOUT[thing];
}

/** 她们发来的照片，最新的在前。 */
export async function getAlbum(): Promise<AlbumPhoto[]> {
  return json(await api("/api/album"));
}

/** 一天里的什么时候：屋里的光和窗外的天跟着它变。 */
export type TimeOfDay = "morning" | "day" | "evening" | "night";

/** 按钟点：5–9 点早上，9–17 点白天，17–19 点傍晚，其余是夜里。 */
export function timeOfDay(at: Date): TimeOfDay {
  const hour = at.getHours();
  if (hour >= 5 && hour < 9) return "morning";
  if (hour >= 9 && hour < 17) return "day";
  if (hour >= 17 && hour < 19) return "evening";
  return "night";
}

/** 屋里能点的东西：每样通到应用里的一个地方，或者就在屋里打开（相册、窗帘）。 */
export type HomeThing = "mailbox" | "diary" | "shelf" | "phone" | "calendar" | "drawer" | "frame" | "window";

/** 点了去哪一页；相框和窗户在屋里就办了，是 null。 */
export const THING_GOES_TO: Record<HomeThing, string | null> = {
  mailbox: "/letters",
  diary: "/diary",
  shelf: "/keepsakes",
  phone: "/conversations",
  calendar: "/calendar",
  drawer: "/gifts",
  frame: null,
  window: null,
};

/** 架子上摆几件纪念品（最近的几件）。 */
export const SHELF_SPACES = 5;

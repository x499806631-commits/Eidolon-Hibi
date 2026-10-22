// 纪念品（纪念品柜 `/keepsakes`，GLOSSARY.md「纪念品」）：偶像送给制作人的东西。后端只给已经送到的，最新的在前；
// 没有图的用 emoji 代替，铺她的壁纸色。打开柜子就算都看过了（`POST /api/keepsakes/seen`）。导航「纪念品」上的
// 圆点由 `unseenKeepsakes` 管，和信箱的一样每分钟读一次。配了生图以后，可以一键把缺的图补上。

import { api, expectNoContent, json, type Segment } from "@/lib/api";
import { UnreadLetters } from "@/lib/letters";

/** 她为什么送：重要的一幕、纪念日、制作人的生日、回礼、约定做到了、出外景的土特产、节日的信。 */
export type KeepsakeOccasion = "scene" | "anniversary" | "birthday" | "gift" | "promise" | "souvenir" | "holiday" | "bond" | "party";

export type Keepsake = {
  id: number;
  character_id: string;
  occasion: KeepsakeOccasion;
  /** 缘由的补充：哪份礼物、哪个约定、从哪里回来…… */
  about: string;
  name: string;
  /** 它的样子，一两句。 */
  look: string;
  /** 没有图时代替它的 emoji。 */
  emoji: string;
  /** 她送的时候说的话。 */
  said: Segment[];
  has_image: boolean;
  /** 随哪封信送来；在对话里送的是 null。 */
  letter_id: number | null;
  given_at: string;
  seen: boolean;
  /** 图重画过几次：每次一个新地址，浏览器不会拿旧图。 */
  drawn: number;
};

export type Cabinet = {
  keepsakes: Keepsake[];
  /** 还没有图的件数。 */
  missing: number;
  /** 正在补图。 */
  filling: boolean;
  /** 部署者配了生图，能补。 */
  can_draw: boolean;
};

export async function getCabinet(): Promise<Cabinet> {
  return json(await api("/api/keepsakes"));
}

export async function getKeepsake(id: number): Promise<Keepsake> {
  return json(await api(`/api/keepsakes/${id}`));
}

export async function seeKeepsakes(): Promise<void> {
  await expectNoContent(await api("/api/keepsakes/seen", { method: "POST" }));
}

export async function fillImages(): Promise<Cabinet & { started: boolean }> {
  return json(await api("/api/keepsakes/fill-images", { method: "POST" }));
}

/** 它的图；有图（`has_image`）时才有。 */
export function keepsakeImageUrl(keepsake: Keepsake): string {
  const base = `/api/keepsakes/${keepsake.id}/image`;
  return keepsake.drawn ? `${base}?v=${keepsake.drawn}` : base;
}

/** 把这件的图重画一张（按现在的写法），给回重画后的它。 */
export async function redrawKeepsake(id: number): Promise<Keepsake> {
  return json(await api(`/api/keepsakes/${id}/redraw`, { method: "POST" }));
}

/** 从柜子里拿掉，对话里那张礼物卡也一起去掉。 */
export async function removeKeepsake(id: number): Promise<void> {
  await expectNoContent(await api(`/api/keepsakes/${id}`, { method: "DELETE" }));
}

async function getUnseen(): Promise<number> {
  const { unseen } = await json<{ unseen: number }>(await api("/api/keepsakes/unseen"));
  return unseen;
}

/** 只看某位偶像送的（`characterId` 为 null 是全部）。 */
export function ofIdol(keepsakes: Keepsake[], characterId: string | null): Keepsake[] {
  return characterId === null ? keepsakes : keepsakes.filter((k) => k.character_id === characterId);
}

/** 送过东西的偶像，按 `order`（偶像的顺序）排，每位一次。 */
export function giversIn(keepsakes: Keepsake[], order: string[]): string[] {
  const giving = new Set(keepsakes.map((k) => k.character_id));
  return order.filter((id) => giving.has(id));
}

/** 送到的日子：年、月、日，按浏览器的时区。 */
export function givenOn(keepsake: Keepsake): { year: number; month: number; day: number } {
  const at = new Date(keepsake.given_at);
  return { year: at.getFullYear(), month: at.getMonth() + 1, day: at.getDate() };
}

export const unseenKeepsakes = new UnreadLetters(getUnseen);

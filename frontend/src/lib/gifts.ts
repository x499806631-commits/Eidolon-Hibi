// 礼物（GLOSSARY.md「礼物」「抽奖」「重置卡」）：礼物页读到的状态，和页上那几句话怎么拼、我的礼物怎么筛。
// 抽奖次数每天在制作人的时区 0 点发 1 次（没设时区的按北京时间），最多攒 7 次，后端读取时补发；做每日任务给的不受这个上限约束；下次什么时候到账
// 由后端给出，按制作人的时区写成「今天 / 明天 HH:MM」，写法和冷却解除时刻一样。
// 送礼一次一件，送出后页面底部那条提示的话也在这里拼。对话页上的送礼入口跳到礼物页时带着谁、从哪段对话来。
// 重置卡自己用：把所有低于信任的角色拉回信任，重置卡条上那句话也在这里拼。

import { api, json } from "@/lib/api";
import { cueText, untilText, type AffinityChange, type AffinityStatus, type Tier } from "@/lib/affinity";
import type { Achievement } from "@/lib/achievements";
import type { DailyTasks } from "@/lib/dailyTasks";
import type { Rewarded } from "@/lib/rewards";
import { copy } from "@/i18n";
import type { HeldTicket, TicketKind } from "@/lib/tickets";

/** 礼物的类别：吃的、饮品、花草、小物件。 */
export type Category = "food" | "drink" | "flower" | "trinket";

/** 稀有度：普通、少见、稀有。 */
export type Rarity = "common" | "uncommon" | "rare";

/** 目录里的一件礼物。下架的（`retired`）不再从抽奖里出，持有的照样显示、照样能送。不带谁喜欢它。 */
export type GiftInfo = { id: string; name: string; category: Category; rarity: Rarity; retired: boolean };

/** 一位角色现在的好感度，送礼时选人用：档位、是否冷却、冷却的解除时刻（ISO，不在冷却是 null）。 */
export type GiftCharacter = { id: string; tier: Tier; cooling: boolean; cooldown_until: string | null };

/** 现在用重置卡会拉回的一位：她现在的档位（疏远或冷淡）、是否冷却。 */
export type Resettable = { id: string; tier: Tier; cooling: boolean };

/** 背包里的一件礼物和它的件数。 */
export type Owned = { gift_id: string; count: number };

/** 读礼物状态接口给的。没开放（`open` 为 false）时照样给已有的次数、背包和重置卡，角色是空的。 */
export type GiftState = {
  open: boolean;
  draws: number;
  max_draws: number;
  /** 每天发几次（称号多了会多，GLOSSARY.md「称号」）；旧的后端没有这一项。 */
  draws_per_day?: number;
  /** 只列持有的，按目录的顺序。 */
  backpack: Owned[];
  reset_cards: number;
  /** 持有的体验券（GLOSSARY.md「体验券」），每种几张。 */
  tickets: HeldTicket[];
  /** 最多再抽几次一定出稀有礼物（GLOSSARY.md「保底」）。 */
  to_rare: number;
  /** 下次发抽奖次数的时刻（ISO，制作人那边的 0 点），攒满了也照样给；没开放时是 null。 */
  next_draw_at: string | null;
  /** 现在用重置卡会拉回的角色；没开放时是空的。 */
  resettable: Resettable[];
  characters: GiftCharacter[];
  /** 今天的每日任务；没开放时是 null。 */
  daily_tasks: DailyTasks | null;
  /** 合起来的四个成就（lib/achievements）；没开放时是 null。 */
  achievements: Achievement[] | null;
  /** 最近一次到账的时刻（ISO），导航「礼物」上的点靠它；从来没有过是 null。 */
  rewarded_at: string | null;
};

/** 抽一次抽到的：一件礼物、一张重置卡，或一张体验券。 */
export type Drawn = { kind: "gift"; gift_id: string } | { kind: "reset_card" } | { kind: "ticket"; ticket: TicketKind };

/** 抽奖接口给的：按抽出的顺序排的结果、抽完还剩几次（连同这次到账的），和这次抽奖到账了什么（没有是 null）。 */
export type DrawResult = { drawn: Drawn[]; draws: number; rewarded: Rewarded | null };

/** 读礼物状态；后端读的时候把到今天（制作人那边的日期）为止该发的次数补上。 */
export async function getGiftState(): Promise<GiftState> {
  return json(await api("/api/gifts"));
}

/** 读礼物目录（含下架的）。 */
export async function getGiftCatalog(): Promise<GiftInfo[]> {
  return json(await api("/api/gifts/catalog"));
}

/** 抽一次，或 `all` 时把攒下的全部抽完。没开放时抛 `giftsClosed` 的 ApiError。 */
export async function drawGifts(all: boolean): Promise<DrawResult> {
  return json(
    await api("/api/gifts/draws", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ all }),
    }),
  );
}

/**
 * 用一张重置卡。没开放时抛 `giftsClosed` 的 ApiError；没有卡了、现在没有低于信任的角色
 * （确认框打开期间她自己回到了信任）是 409，都不扣卡。
 */
export async function spendResetCard(): Promise<void> {
  // 接口还给拉回了谁、剩几张；礼物页用完重新读一遍状态，不看这些。
  await json(await api("/api/gifts/resets", { method: "POST" }));
}

/** 送礼接口给的：记录放进了哪段对话、这次的变化（没有胶囊时是 null）、她现在的好感度、这件还剩几件、这次到账了什么（没有是 null）。 */
export type Given = {
  conversation_id: string;
  change: AffinityChange | null;
  affinity: AffinityStatus & { cooling: boolean };
  left: number;
  rewarded: Rewarded | null;
};

/**
 * 把一件礼物送给她；记录进她的当前对话，当前对话已结束就进一段新开的（GLOSSARY.md「礼物」）。
 * 没开放时抛 `giftsClosed` 的 ApiError，这件已经没有了（另一个标签页刚送掉最后一件）是 409。
 */
export async function giveGift(characterId: string, giftId: string): Promise<Given> {
  return json(
    await api(`/api/characters/${encodeURIComponent(characterId)}/gifts`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ gift_id: giftId }),
    }),
  );
}

/**
 * 送出后底部提示的那句话：有变化时就是送礼记录后面那个变化胶囊的话（有点开心、更近了一步、愿意说话了）；
 * 冷却中送了还没解除时写解除时刻（按制作人的时区，写法和对话页冷却说明一致）；到顶了只写已经送到，不提示已满。
 */
export function givenNote(given: Given, name: string, now: Date, timeZone: string): string {
  const t = copy.gifts.given;
  if (given.change) return cueText(given.change, name);
  const until = given.affinity.cooldown_until;
  return until ? t.stillCooling(name, untilText(new Date(until), now, timeZone)) : t.given(name);
}

/** 从对话页跳来的礼物页：预选的她。 */
export type GiftsFor = { to: string };

/** 对话页上「送她一件礼物」去的地方：礼物页，带上她的角色 id。不带对话：礼物一律记进她的当前对话（GLOSSARY.md「礼物」）。 */
export function giftsHref(characterId: string): string {
  return `/gifts?${new URLSearchParams({ to: characterId })}`;
}

/** 礼物页地址里的预选；没有 `to`（或者不止一个、是空的）就是没有预选。`to` 是不是真有这位角色由礼物页判断。
 * 以前的链接还带着 `conversation`，不管它。 */
export function giftsFor(query: Record<string, string | string[] | undefined>): GiftsFor | null {
  const { to } = query;
  if (typeof to !== "string" || to === "") return null;
  return { to };
}

/** 送给谁的顺序：预选的她（`to`）排第一，其余照原来的顺序；没有预选或者她不在里面时不变。 */
export function chosenFirst<C extends { id: string }>(characters: C[], to: string | null): C[] {
  const chosen = characters.find((c) => c.id === to);
  return chosen ? [chosen, ...characters.filter((c) => c !== chosen)] : characters;
}

/** 下次发抽奖次数的时刻按制作人的时区写成「今天 HH:MM」或「明天 HH:MM」（不会超过 24 小时）。 */
export function nextDrawText(nextDrawAt: string, now: Date, timeZone: string): string {
  return untilText(new Date(nextDrawAt), now, timeZone);
}

/** 抽奖台的小点：亮几个（不超过上限），超过上限（每日任务给的）时后面写超出几次（「+2」，没超过是 null），读屏怎么读。 */
export function drawDots(draws: number, max: number): { lit: number; more: string | null; label: string } {
  const t = copy.gifts.draw;
  return draws > max
    ? { lit: max, more: t.more(draws - max), label: t.over(draws) }
    : { lit: draws, more: null, label: t.label(draws, max) };
}

/** 抽奖台小点下面那句：用完了、攒满了（每日任务给的超过上限也算）、平时三种；`next` 是 `nextDrawText` 写好的下次到账时刻。 */
export function drawNote(draws: number, max: number, next: string, perDay = 1): string {
  const t = copy.gifts.draw.notes;
  if (draws <= 0) return t.none(next, perDay);
  if (draws >= max) return t.full;
  return t.usual(next, max, perDay);
}

/** 重置卡条上那句：现在用会拉回几位，一位都没有时留着以后用。 */
export function resetNote(resettable: number): string {
  const t = copy.gifts.resetCards.notes;
  return resettable > 0 ? t.some(resettable) : t.none;
}

/** 「还没有礼物」下面那句：有次数时叫人抽一次，没有时说下次什么时候发（`next`，同 `drawNote`）。 */
export function emptyNote(draws: number, next: string): string {
  return draws > 0 ? copy.gifts.empty.withDraws : copy.gifts.empty.withoutDraws(next);
}

/** 我的礼物的筛选：全部，或某一类。 */
export type GiftFilter = "all" | Category;

export const GIFT_FILTERS: readonly GiftFilter[] = ["all", "food", "drink", "flower", "trinket"];

/** 持有的一件礼物和件数。 */
export type Held = { gift: GiftInfo; count: number };

/** 我的礼物网格里列的：背包里属于 `filter` 这一类的，按背包的顺序；目录里找不到的不列。 */
export function shownGifts(backpack: Owned[], catalog: GiftInfo[], filter: GiftFilter): Held[] {
  const byId = new Map(catalog.map((g) => [g.id, g]));
  return backpack.flatMap(({ gift_id, count }) => {
    const gift = byId.get(gift_id);
    return gift && (filter === "all" || gift.category === filter) ? [{ gift, count }] : [];
  });
}

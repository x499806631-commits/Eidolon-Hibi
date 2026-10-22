// 拜托（GLOSSARY.md「拜托」）：用一张拜托券，再交出背包里的几件礼物当材料，请一位偶像做一件事：
// 换个称呼、叫起床、结拜、托人和好、一起办聚会。每种要哪些礼物（`Slot`）由后端给；
// 这里挑出背包里够格的、先替制作人选好，谁能拜托（档位）也在这里判断。

import type { Tier } from "@/lib/affinity";
import { api, json } from "@/lib/api";
import type { Category, GiftCharacter, GiftInfo, Owned, Rarity } from "@/lib/gifts";

/** 换个称呼、叫起床、结拜、托人和好、聚会。 */
export type FavorKind = "address" | "wake" | "bond" | "make_up" | "party";

export const FAVOR_KINDS: readonly FavorKind[] = ["address", "wake", "bond", "make_up", "party"];

/** 一件材料：这几类之一（空的是哪类都行），这个稀有度（null 是哪种都行）。 */
export type Slot = { categories: Category[]; rarity: Rarity | null };

/** 结拜时谁年长：她，还是制作人。 */
export type Elder = "her" | "producer";

/** 拜托页读到的：每种要的材料、谁怎么叫你和谁结拜了、要叫你起床的、要办的聚会、一周内的生日。 */
export type FavorOverview = {
  materials: Record<FavorKind, Slot[]>;
  ties: { character_id: string; address: string | null; bond: Elder | null }[];
  wakes: { character_id: string; at: string; left: number }[];
  parties: { day: string; helper_id: string; what: string }[];
  birthdays: { character_id: string; day: string }[];
  max_days_ahead: number;
};

/** 聚会的几个常见主题，也可以自己写。 */
export const PARTY_IDEAS: readonly string[] = ["火锅会", "庆功宴", "赏月会", "游戏之夜", "烤肉会"];

/** 聚会最多请几位（帮忙的不算）。 */
export const MAX_GUESTS = 4;

const NOT_NOW: ReadonlySet<Tier> = new Set(["cold", "distant"]);
const BOND_TIERS: ReadonlySet<Tier> = new Set(["close", "intimate"]);

/** 能拜托她（换称呼、叫起床、帮忙说情、帮忙办聚会）：没有冷淡、疏远，也不在冷却。好感度没开放时都行。 */
export function willing(status: GiftCharacter | undefined): boolean {
  return !status || (!NOT_NOW.has(status.tier) && !status.cooling);
}

/** 能结拜：亲近以上，不在冷却。 */
export function canBond(status: GiftCharacter | undefined): boolean {
  return !status || (BOND_TIERS.has(status.tier) && !status.cooling);
}

/** 闹了别扭、可以托人和好的：冷淡、疏远或冷却中。 */
export function fallenOut(status: GiftCharacter | undefined): boolean {
  return !!status && (NOT_NOW.has(status.tier) || status.cooling);
}

/** 这件礼物能不能当这件材料。 */
export function fits(slot: Slot, gift: GiftInfo): boolean {
  return (slot.categories.length === 0 || slot.categories.includes(gift.category)) && (slot.rarity === null || gift.rarity === slot.rarity);
}

/**
 * 替制作人先选好的材料：每件按顺序选背包里第一件够格、还没被前面选光的；选不出的是 null。
 * `kept` 是已经选的（换了选项的保留，没选过或已经不够格的重选）。
 */
export function pickMaterials(slots: Slot[], backpack: Owned[], catalog: Map<string, GiftInfo>, kept: (string | null)[] = []): (string | null)[] {
  const left = new Map(backpack.map((o) => [o.gift_id, o.count]));
  const picked: (string | null)[] = [];
  slots.forEach((slot, i) => {
    const wanted = kept[i];
    const keep = wanted && (left.get(wanted) ?? 0) > 0 && catalog.get(wanted) && fits(slot, catalog.get(wanted)!) ? wanted : null;
    const choice = keep ?? backpack.find((o) => (left.get(o.gift_id) ?? 0) > 0 && catalog.get(o.gift_id) && fits(slot, catalog.get(o.gift_id)!))?.gift_id ?? null;
    if (choice) left.set(choice, (left.get(choice) ?? 0) - 1);
    picked.push(choice);
  });
  return picked;
}

/** 这件材料可以选哪些：够格的、算上别的材料选掉的还有剩的（自己选的那件也在）。 */
export function choicesFor(index: number, slots: Slot[], picked: (string | null)[], backpack: Owned[], catalog: Map<string, GiftInfo>): GiftInfo[] {
  return backpack.flatMap((o) => {
    const gift = catalog.get(o.gift_id);
    if (!gift || !fits(slots[index], gift)) return [];
    const usedElsewhere = picked.filter((p, i) => i !== index && p === o.gift_id).length;
    return o.count - usedElsewhere > 0 ? [gift] : [];
  });
}

export async function getFavors(): Promise<FavorOverview> {
  return json(await api("/api/favors"));
}

async function ask(kind: FavorKind, body: object): Promise<{ conversation_id: string; left: number }> {
  return json(
    await api(`/api/favors/${kind}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

/** 换个称呼；`address` 是 null 时改回她原来的叫法。 */
export function askAddress(characterId: string, address: string | null, giftIds: string[]) {
  return ask("address", { character_id: characterId, address, gift_ids: giftIds });
}

/** 叫起床：`at` 是 HH:MM，从下一个早上起连续 `days` 天。 */
export function askWake(characterId: string, at: string, days: number, giftIds: string[]) {
  return ask("wake", { character_id: characterId, at, days, gift_ids: giftIds });
}

/** 结拜，`elder` 是年长的一方。 */
export function askBond(characterId: string, elder: Elder, giftIds: string[]) {
  return ask("bond", { character_id: characterId, elder, gift_ids: giftIds });
}

/** 托 `mediatorId` 去和 `targetId` 说情，礼物由她转交。 */
export function askMakeUp(mediatorId: string, targetId: string, giftIds: string[]) {
  return ask("make_up", { mediator_id: mediatorId, target_id: targetId, gift_ids: giftIds });
}

/** 请 `helperId` 帮忙办聚会；`honoreeId` 是过生日的那位（生日会时）。`day` 是 YYYY-MM-DD。 */
export function askParty(helperId: string, guestIds: string[], what: string, honoreeId: string | null, day: string, giftIds: string[]) {
  return ask("party", { helper_id: helperId, guest_ids: guestIds, what, honoree_id: honoreeId, day, gift_ids: giftIds });
}

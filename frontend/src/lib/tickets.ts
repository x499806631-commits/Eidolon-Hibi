// 体验券（GLOSSARY.md「体验券」）：抽奖偶尔抽到的、请一位偶像为你做点什么的券。每种一个 kind，
// 在礼物页用（日记钥匙在日记页上用），用掉一张；没用成会还回来。新加一种券：后端加一种，这里加一种。

import { api, json } from "@/lib/api";
import type { DiaryEntry } from "@/lib/diary";

/** 指定礼物、来信、日记钥匙、拍照、邀约。 */
export type TicketKind = "liked_gift" | "letter" | "diary_key" | "photo" | "date";

export const TICKET_KINDS: readonly TicketKind[] = ["liked_gift", "letter", "diary_key", "photo", "date"];

/** 持有的一种券和张数。 */
export type HeldTicket = { kind: TicketKind; count: number };

/** 拍照券想要的衣服：衣装库里的一套（卡名、私服还是演出服）、自己描述的，或者都不给、让她挑。 */
export type PhotoWish =
  | { outfit_id: string; card: string; look: "everyday" | "stage" }
  | { described: string }
  | Record<string, never>;

/** 邀约的选项：常见的几件事，也可以自己写。 */
export const DATE_IDEAS: readonly string[] = ["逛街", "看电影", "去咖啡店", "去游乐园", "去水族馆", "散步"];

/** 邀约能约的最远一天：一周以内。 */
export const MAX_DAYS_AHEAD = 7;

async function redeem<T>(kind: TicketKind, body: object): Promise<T & { left: number }> {
  return json(
    await api(`/api/tickets/${kind}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}

/** 指定礼物：得到她喜欢的哪一件。 */
export function redeemLikedGift(characterId: string) {
  return redeem<{ gift_id: string }>("liked_gift", { character_id: characterId });
}

/** 来信：她现在就写，进信箱。 */
export function redeemLetter(characterId: string) {
  return redeem<object>("letter", { character_id: characterId });
}

/** 日记钥匙：打开这一页，给回打开后的样子。 */
export function redeemDiaryKey(entryId: number) {
  return redeem<{ entry: DiaryEntry }>("diary_key", { entry_id: entryId });
}

/** 拍照：她在对话里发来，图随后画好。 */
export function redeemPhoto(characterId: string, wish: PhotoWish) {
  return redeem<{ conversation_id: string }>("photo", { character_id: characterId, ...wish });
}

/** 邀约：她马上回你。`day` 是 YYYY-MM-DD。 */
export function redeemDate(characterId: string, what: string, day: string) {
  return redeem<{ conversation_id: string }>("date", { character_id: characterId, what, day });
}

/** 从今天起往后 `ahead` 天的日子（YYYY-MM-DD），按 `timeZone`。 */
export function dayAhead(now: Date, ahead: number, timeZone: string): string {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" })
    .format(now)
    .split("-")
    .map(Number);
  const day = new Date(Date.UTC(parts[0], parts[1] - 1, parts[2] + ahead));
  return day.toISOString().slice(0, 10);
}

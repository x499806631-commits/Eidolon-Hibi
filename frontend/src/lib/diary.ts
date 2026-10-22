// 日记（日记页 `/diary`，GLOSSARY.md「日记」）：偶像在对她来说重要的那天晚上写的日记。后端按她现在的好感度
// 只给能看的部分（`shown`），看不到的字根本不会到浏览器。打开一篇就算读过（合上的不算）。导航「日记」上的
// 未读圆点由 `unreadDiary` 管，和信箱的一样每分钟读一次。

import { api, json, type Segment } from "@/lib/api";
import { UnreadLetters } from "@/lib/letters";

/** 能看多少：合上（只有日子）、开头一段、全文、全文加她夹起来的那一页。 */
export type Shown = "closed" | "opening" | "full" | "secret";

export type DiaryEntry = {
  id: number;
  character_id: string;
  /** 她写的是哪一天，制作人的时区，YYYY-MM-DD。 */
  day: string;
  shown: Shown;
  /** 能看的段落；合上时没有。 */
  segments: Segment[];
  /** 后面还有没给看的段落。 */
  more: boolean;
  /** 她夹起来的那一页，只有 `secret` 时才有。 */
  secret: Segment[];
  /** 有一页被夹起来了（全文能看、那一页还不能）。 */
  secret_hidden: boolean;
  /** 用日记钥匙能看到更多（GLOSSARY.md「日记钥匙」）：合上的、只给看开头的、还夹着一页的。 */
  unlockable: boolean;
  read: boolean;
};

export async function getDiary(): Promise<DiaryEntry[]> {
  return json(await api("/api/diary"));
}

export async function readEntry(id: number): Promise<DiaryEntry> {
  return json(await api(`/api/diary/${id}/read`, { method: "POST" }));
}

async function getUnread(): Promise<number> {
  const { unread } = await json<{ unread: number }>(await api("/api/diary/unread"));
  return unread;
}

/** 清单上一行露出的那句：第一段的中文；合上时没有。 */
export function preview(entry: DiaryEntry): string | null {
  const first = entry.segments[0];
  return first ? first.zh.trim() || first.ja.trim() : null;
}

/** 日子：月和日。 */
export function monthDay(entry: DiaryEntry): { month: number; day: number } {
  const [, month, day] = entry.day.split("-").map(Number);
  return { month, day };
}

/** 翻页：同一位偶像的前一天和后一天（都是写过日记的日子），没有就是 null。 */
export function neighbours(
  entries: DiaryEntry[],
  entry: DiaryEntry,
): { previous: DiaryEntry | null; next: DiaryEntry | null } {
  const hers = entries
    .filter((e) => e.character_id === entry.character_id)
    .sort((a, b) => (a.day < b.day ? -1 : a.day > b.day ? 1 : a.id - b.id));
  const at = hers.findIndex((e) => e.id === entry.id);
  return { previous: hers[at - 1] ?? null, next: at < 0 ? null : (hers[at + 1] ?? null) };
}

/** 能看又没读过的才算未读：合上的那篇不算。 */
export function isUnread(entry: DiaryEntry): boolean {
  return !entry.read && entry.shown !== "closed";
}

export const unreadDiary = new UnreadLetters(getUnread);

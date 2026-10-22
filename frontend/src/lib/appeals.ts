// 「我不是这个意思」（GLOSSARY.md）：她最新的那条回复让好感度降了，制作人可以说明自己不是那个意思，
// 那一回合扣掉的退回来，她下一句话时会知道。每天几次，所有偶像一起算。

import { copy } from "@/i18n";
import type { AffinityStatus } from "@/lib/affinity";
import { api, ApiError, json } from "@/lib/api";
import type { Entry } from "@/lib/conversationLog";

export type Appealed = { affinity: AffinityStatus; left: number };

/** 后端拒绝的原因（`appeals.Refusal`）。 */
export type Refusal = "closed" | "no_change" | "not_latest" | "appealed" | "ended" | "cooling" | "none_left";

/** 说明她这条回复（`messageId`）不是那个意思：好感度退回后的样子，和今天还剩几次。 */
export async function appeal(messageId: number): Promise<Appealed> {
  return json(await api(`/api/messages/${messageId}/appeal`, { method: "POST" }));
}

/** 记录里的这条是她存下来的回复时，它的消息 id；她的搭话、还没存下的回复都没有。 */
export function messageIdOf(entry: Entry): number | null {
  if (entry.kind !== "character" || entry.reachOut) return null;
  const match = /^m(\d+)$/.exec(entry.key);
  return match ? Number(match[1]) : null;
}

/** 申诉没成时给制作人看的一句话。 */
export function appealFailure(error: unknown): string {
  const t = copy.conversation.affinity.appeal;
  const code = error instanceof ApiError ? error.code : null;
  return code && code in t.refused ? t.refused[code as Refusal] : t.failed;
}

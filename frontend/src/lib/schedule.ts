// 她现在大概在干嘛（GLOSSARY.md「行程」）：对话页页首她名字下面那一行。后端按制作人的时区给：
// 在某一段安排里就是那一段（到几点），两段之间是「有空」，今天没有行程就是 null，什么都不显示。

import { copy } from "@/i18n";
import { api, json } from "@/lib/api";

export type ScheduleNow =
  | { what: string; where: string; kind: string; until: string }
  | { free: true }
  | null;

export async function getScheduleNow(characterId: string): Promise<ScheduleNow> {
  return json(await api(`/api/characters/${encodeURIComponent(characterId)}/schedule/now`));
}

/** 名字下面那一行的字；没有行程时是 null。 */
export function scheduleText(now: ScheduleNow): string | null {
  const t = copy.conversation.schedule;
  if (now === null) return null;
  if ("free" in now) return t.free;
  return now.where ? t.doingAt(now.what, now.where) : t.doing(now.what);
}

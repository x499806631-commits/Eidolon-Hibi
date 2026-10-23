// 约会（GLOSSARY.md「约会」）：约好的那天，当面去赴约。约会的画面盖在她的对话页上，说的话照常是这段对话的回合；
// 这里是去赴约、下一站、合照、结束、背景和立绘表情的接口，和画面里用的几样小工具。

import { api, json, type Segment } from "@/lib/api";
import type { Emotion } from "@/lib/standAnimations";

export type OutingStop = { id: string; name: string };

export type Outing = {
  id: number;
  character_id: string;
  day: string;
  what: string;
  /** 地图上的哪一处（`lib/cityMap` 的 DateSpot），没说到地图上的地方是 null（在街上随便逛）。 */
  spot: string | null;
  conversation_id: string;
  stops: OutingStop[];
  /** 现在在第几站，从 0 起。 */
  stop: number;
  started_at: string;
  /** 比约好的时间段晚到了几分钟；准时是 0。 */
  late_minutes: number;
  ended_at: string | null;
  photos: number;
  max_photos: number;
  /** 结束后她自己的回顾；没结束是 null。 */
  recap: string | null;
};

/** 今天和她约好的约会：做什么、地图上的哪一处、什么时候去最好、现在能不能去。 */
export type PlannedDate = {
  day: string;
  what: string;
  spot: string | null;
  opens: string;
  best_from: string;
  best_until: string;
  closes: string;
  can_start: boolean;
};

export type OutingToday = { date: PlannedDate | null; outing: Outing | null; can_draw: boolean };

export async function getOutingToday(characterId: string): Promise<OutingToday> {
  return json(await api(`/api/outings/today?character=${encodeURIComponent(characterId)}`));
}

async function post<T>(path: string, body: object = {}): Promise<T> {
  return json(
    await api(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) }),
  );
}

export function meet(characterId: string): Promise<Outing> {
  return post("/api/outings", { character_id: characterId });
}

export function nextStop(outingId: number): Promise<Outing> {
  return post(`/api/outings/${outingId}/next`);
}

export function takePhoto(outingId: number, wish: string): Promise<Outing> {
  return post(`/api/outings/${outingId}/photo`, { wish });
}

export function endOuting(outingId: number): Promise<Outing> {
  return post(`/api/outings/${outingId}/end`);
}

export function drawStop(outingId: number, again = false): Promise<{ drawn: boolean }> {
  return post(`/api/outings/${outingId}/background`, { again });
}

/** 这一站画好的背景；没画过时请求是 404，用代码画的插画。`v` 让重画以后换一个地址。 */
export function stopPictureUrl(spot: string | null, stop: string, v = 0): string {
  return `/api/outings/background/${encodeURIComponent(spot ?? "around")}/${encodeURIComponent(stop)}?v=${v}`;
}

export type JudgedSegment = { emotion: Emotion; nod: "yes" | "no" | null };

/** 她说这一回复每一段时的表情（同通话里的立绘），没有判断模型时都是平静。 */
export function judgeEmotions(characterId: string, userSaid: string, segments: Segment[]): Promise<JudgedSegment[]> {
  return post("/api/outings/emotions", {
    character_id: characterId,
    user_said: userSaid,
    segments: segments.slice(0, 20).map((s) => ({ ja: s.ja, zh: s.zh })),
  });
}

/** 预设的小动作（GLOSSARY.md「约会」），点了就当面做这件事；「其它」自己写。 */
export const ACTIONS: readonly string[] = ["牵起她的手", "递给她一杯饮料", "夸她今天很好看", "轻轻摸摸她的头"];

/** 小动作作为这一回合说的话：后端在约会时告诉她，（动作：……）写的是制作人当面做的事。 */
export function actionText(action: string, said = ""): string {
  const what = action.trim();
  return said.trim() ? `（动作：${what}）${said.trim()}` : `（动作：${what}）`;
}

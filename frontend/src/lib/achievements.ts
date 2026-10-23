// 成就（GLOSSARY.md「成就」）：她的对话页面板和礼物页那一小节里，每一行怎么写。一个成就分几个阶段，按序号解锁，
// 第 n 阶段给 n 次抽奖；她按角色算的阶段全部解锁叫集齐，另给重置卡。门槛、奖励、解锁了哪几个都由后端给，这里只管
// 写成一行：名字、阶段点、只写下一个阶段的进度、下一阶段的奖励。档位只有档位名，不给好感度的数值。
//
// 新解锁的点：看过只记在这台设备上，和导航「礼物」上的点同一套存法（lib/seenOnDevice）：按用户、按范围（某位角色的
// id，或者合起来的那几个）存看过的最近解锁时刻（eidolon.achievementsSeen.<用户 id>.<范围>）；还没存过时任何解锁都算
// 没看过（上线补算出来的照样出点）。

import type { Tier } from "@/lib/affinity";
import { api, json } from "@/lib/api";
import type { Rewarded } from "@/lib/rewards";
import { isUnseen, keepSeenOnDevice, later, seenOnDevice } from "@/lib/seenOnDevice";
import { copy } from "@/i18n";

/** 一个阶段：门槛（档位类是档位名）、解锁时给几次抽奖、解锁时刻（ISO，没解锁是 null）。 */
export type AchievementStage = { goal: number | Tier; draws: number; unlocked_at: string | null };

/** 一个成就：id、累计进度（通话是秒，档位类是到过的最高档位、一个没到过是 null）、按序号排的阶段。 */
export type Achievement = { id: string; progress: number | Tier | null; stages: AchievementStage[] };

/** 集齐那一行：集齐的时刻（ISO，没集齐是 null）、集齐给几张重置卡（这位角色的已经给过就是 0，一辈子只给一次）。 */
export type Complete = { completed_at: string | null; reset_cards: number };

/** 读她的成就接口给的：她按角色算的四个，和集齐那一行。开关关着时接口给 null。 */
export type HerAchievements = { achievements: Achievement[]; complete: Complete };

/** 读她的成就；「好感度和礼物」关着时是 null。隐藏的角色照样读得到。 */
export async function getHerAchievements(characterId: string): Promise<HerAchievements | null> {
  return json(await api(`/api/characters/${encodeURIComponent(characterId)}/achievements`));
}

/** 一行：名字、阶段点（解锁的为 true）、全部解锁没有、下一个阶段的进度（全部解锁时写「全部解锁」）、下一阶段的奖励（全部解锁是 null）。 */
export type AchievementRow = {
  id: string;
  name: string;
  stages: boolean[];
  isAllUnlocked: boolean;
  progress: string;
  reward: string | null;
};

/** 集齐那一行：集齐了没有、「集齐」或「集齐了」、没集齐而且还会给时右边的「重置卡 ×n」。 */
export type CompleteRow = { isComplete: boolean; name: string; reward: string | null };

/**
 * 一个成就那一行怎么写。下一个阶段是第一个没解锁的（门槛调低以后进度可能已经过了它，到下一次往来才解锁）。
 * 后端给了不认识的成就也照样列出，名字用 id，进度只写数字。
 */
export function achievementRow(achievement: Achievement): AchievementRow {
  const t = copy.achievements;
  const stages = achievement.stages.map((s) => s.unlocked_at !== null);
  const next = achievement.stages.find((s) => s.unlocked_at === null);
  return {
    id: achievement.id,
    name: t.names[achievement.id] ?? achievement.id,
    stages,
    isAllUnlocked: next === undefined,
    progress: next === undefined ? t.allUnlocked : progressText(achievement.id, achievement.progress, next.goal),
    reward: next === undefined ? null : t.draws(next.draws),
  };
}

/** 集齐那一行怎么写。 */
export function completeRow(complete: Complete): CompleteRow {
  const t = copy.achievements;
  const isComplete = complete.completed_at !== null;
  return {
    isComplete,
    name: isComplete ? t.completed : t.complete,
    reward: isComplete || complete.reset_cards === 0 ? null : t.resetCards(complete.reset_cards),
  };
}

/** 进度文字：「420 个回合 / 1000 个回合」；档位类只写「下一个：亲密」。 */
function progressText(id: string, progress: Achievement["progress"], goal: AchievementStage["goal"]): string {
  const t = copy.achievements;
  if (typeof goal === "string") return t.nextTier(copy.conversation.affinity.tiers[goal]);
  const amount = typeof progress === "number" ? progress : 0;
  const unit = id === "call_time" ? duration : (t.units[id] ?? String);
  return `${unit(amount)} / ${unit(goal)}`;
}

/** 通话时长（秒）：不到一小时写「n 分钟」，否则「3 小时 12 分」，整小时只写「5 小时」。 */
function duration(seconds: number): string {
  const t = copy.achievements.duration;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return t.minutes(minutes);
  const rest = minutes % 60;
  return rest ? t.hoursMinutes(Math.floor(minutes / 60), rest) : t.hours(minutes / 60);
}

function seenKey(userId: string, scope: string): string {
  return `eidolon.achievementsSeen.${userId}.${scope}`;
}

/** 这位用户在这台设备上看过的、这个范围（她的 id，或者合起来的那几个）的最近解锁时刻；没看过为 null。 */
export function achievementsSeenUpTo(userId: string, scope: string): string | null {
  return seenOnDevice(seenKey(userId, scope));
}

/** 记下这位用户看过了这个范围 `at` 为止的解锁；别的标签页已经记下更晚的，就留着那个。 */
export function keepAchievementsSeenUpTo(userId: string, scope: string, at: string): void {
  keepSeenOnDevice(seenKey(userId, scope), at);
}

/** 合起来的那几个（礼物页那一小节）记看过时的范围；角色的 id 里没有这个。 */
export const OF_ALL = "all";

/** 面板里集齐那一行在 `unseenRows` 里的 id。 */
export const COMPLETE = "complete";

/** 面板或礼物页那一小节列出的几个成就，和集齐那一行（她的面板里才有）。 */
export type Listed = { achievements: Achievement[]; complete?: Complete };

/** 这几个成就（连同集齐那一行，有的话）里最近一次解锁的时刻；一个都没解锁是 null。 */
export function latestUnlock({ achievements, complete }: Listed): string | null {
  const times = achievements.flatMap((a) => a.stages.map((s) => s.unlocked_at));
  return [...times, complete?.completed_at ?? null].reduce(later, null);
}

/** 带点的那几行（成就的 id，集齐那一行是 `COMPLETE`）：有阶段的解锁晚于看过的（`seenAt`）。 */
export function unseenRows({ achievements, complete }: Listed, seenAt: string | null): Set<string> {
  const rows = achievements
    .filter((a) => a.stages.some((s) => isUnseen(s.unlocked_at, seenAt)))
    .map((a) => a.id);
  if (complete && isUnseen(complete.completed_at, seenAt)) rows.push(COMPLETE);
  return new Set(rows);
}

/**
 * 送礼、抽奖的返回里带着合起来的阶段（`character_id` 为 null）时，不等重读就把它们标成解锁：解锁时刻就是这次到账的
 * （后端解锁和到账在同一个事务里、用同一个时刻），进度至少到那个阶段的门槛。这次没有到账时原样返回。
 */
export function withUnlocked(achievements: Achievement[], rewarded: Rewarded | null): Achievement[] {
  const unlocked = (rewarded?.achievements ?? []).filter((u) => u.character_id === null);
  if (!rewarded || unlocked.length === 0) return achievements;
  return achievements.map((a) => {
    const stages = unlocked.filter((u) => u.achievement === a.id).map((u) => u.stage);
    if (stages.length === 0) return a;
    const reached = Math.max(...stages);
    const goal = a.stages[reached - 1]?.goal;
    return {
      ...a,
      progress: typeof goal === "number" && typeof a.progress !== "string" ? Math.max(a.progress ?? 0, goal) : a.progress,
      stages: a.stages.map((s, i) =>
        stages.includes(i + 1) && s.unlocked_at === null ? { ...s, unlocked_at: rewarded.rewarded_at } : s,
      ),
    };
  });
}

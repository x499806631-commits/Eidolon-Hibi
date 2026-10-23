// 到账：做完一项每日任务（GLOSSARY.md「每日任务」），抽奖次数自动给到用户（成就的奖励也走这里）。回合的 `done`、
// 送礼的返回带着「这次到账」；礼物状态带着最近一次到账的时刻。导航栏和 dock 的「礼物」上那个点意思是「有新到的
// 抽奖次数」：开关开着、最近一次到账晚于这台设备上看过的，就亮。
//
// 看过只记在这台设备上（lib/seenOnDevice）：按用户分开存（eidolon.rewardsSeen.<用户 id>），存的是看过的最近一次到账时刻。

import { isUnseen, keepSeenOnDevice, seenOnDevice } from "@/lib/seenOnDevice";

/** 这次解锁的一个成就阶段（lib/achievements）：哪个成就、哪位角色（合起来的是 null）、第几阶段、给了几次抽奖。 */
export type UnlockedStage = { achievement: string; character_id: string | null; stage: number; draws: number };

/** 这次集齐的一位角色，和给了几张重置卡。 */
export type CompletedCharacter = { character_id: string; reset_cards: number };

/**
 * 这次到账了什么：抽奖次数、重置卡张数、做完了哪几项每日任务（id）、解锁了哪些成就阶段、集齐了哪几位角色，
 * 和这位用户最近一次到账的时刻（ISO，服务器时间）。
 */
export type Rewarded = {
  draws: number;
  reset_cards: number;
  daily_tasks: string[];
  achievements: UnlockedStage[];
  completed: CompletedCharacter[];
  rewarded_at: string;
};

function seenKey(userId: string): string {
  return `eidolon.rewardsSeen.${userId}`;
}

/** 这位用户在这台设备上看过的最近一次到账时刻；没看过为 null。 */
export function rewardsSeenUpTo(userId: string): string | null {
  return seenOnDevice(seenKey(userId));
}

/** 记下这位用户看过了 `at` 为止的到账；别的标签页已经记下更晚的，就留着那个。 */
export function keepRewardsSeenUpTo(userId: string, at: string): void {
  keepSeenOnDevice(seenKey(userId), at);
}

/** 「礼物」上的点亮不亮：开关开着（`isOpen`），而且最近一次到账（`rewardedAt`）晚于看过的（`seenAt`）。 */
export function hasNewRewards({
  isOpen,
  rewardedAt,
  seenAt,
}: {
  isOpen: boolean;
  rewardedAt: string | null;
  seenAt: string | null;
}): boolean {
  return isOpen && isUnseen(rewardedAt, seenAt);
}

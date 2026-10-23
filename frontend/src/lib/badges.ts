// 称号（GLOSSARY.md「称号」）：做过的事、和她们多亲近，满足了就得到，一直都在。

import { api, json } from "@/lib/api";

export type Badge = {
  id: string;
  name: string;
  /** 怎么得到的。 */
  how: string;
  kind: "doing" | "affinity";
  /** 什么时候得到的；还没得到是 null。 */
  earned_at: string | null;
  unseen: boolean;
};

export type Badges = {
  badges: Badge[];
  earned: number;
  /** 现在每天能偶遇几次、每天给几次抽奖。 */
  perk: number;
  /** 再到几个称号能多一次；已经最多是 null。 */
  next: number | null;
};

export async function getBadges(): Promise<Badges> {
  return json(await api("/api/badges"));
}

export async function seeBadges(): Promise<void> {
  await api("/api/badges/seen", { method: "POST" });
}

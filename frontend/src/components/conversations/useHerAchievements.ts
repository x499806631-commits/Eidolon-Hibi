"use client";

import { useCallback, useEffect, useState } from "react";

import {
  achievementsSeenUpTo,
  getHerAchievements,
  keepAchievementsSeenUpTo,
  latestUnlock,
  unseenRows,
  type HerAchievements,
} from "@/lib/achievements";
import { isUnseen, later } from "@/lib/seenOnDevice";

import { useDirectory } from "./ConversationDirectory";

const NONE: ReadonlySet<string> = new Set();

/**
 * 她的对话页上她的成就（spec「『看过』和点」）：页面打开时读一次；外壳知道的最近一次到账（`rewardedAt`，
 * 外壳每分钟轮询，回合带着到账时当场更新）变了就重读，通话挂断后才结算的时长阶段靠这条；这一页上的回合解锁了她的
 * 阶段（`unlockedAt`，`LiveConversation` 快照的 `herUnlockedAt`）时，不等重读就当场亮。当场亮用的是那次到账的时刻：
 * 后端解锁和到账在同一个事务里、用同一个时刻，所以它就是这些阶段的解锁时刻；两者要是分开，打开面板记下的看过会早于
 * 重读到的解锁时刻，关上面板点又亮。
 *
 * - `her`：她的成就；还没读到、读不到或者开关关着时是 null，这时更多菜单里没有「成就」。
 * - `isUnseen`：更多菜单键和菜单项上的点：她的最近解锁晚于这台设备上看过的。
 * - `unseenRows`：面板里带点的那几行：解锁晚于打开面板前看过的；打开面板就记成看过，点留到下一次打开。
 */
export function useHerAchievements(
  characterId: string,
  userId: string,
  unlockedAt: string | null,
): {
  her: HerAchievements | null;
  isUnseen: boolean;
  unseenRows: ReadonlySet<string>;
  isPanelOpen: boolean;
  setPanelOpen: (open: boolean) => void;
} {
  const { rewardedAt } = useDirectory();
  // 读到的连同是谁的：换了角色，上一位的不拿来用。
  const [read, setRead] = useState<{ characterId: string; her: HerAchievements | null } | null>(null);
  const [seen, setSeen] = useState(() => ({ characterId, at: achievementsSeenUpTo(userId, characterId) }));
  const [panel, setPanel] = useState<{ isOpen: boolean; seenBefore: string | null }>({
    isOpen: false,
    seenBefore: null,
  });

  useEffect(() => {
    let gone = false;
    getHerAchievements(characterId).then(
      (her) => !gone && setRead({ characterId, her }),
      () => {}, // 读不到时留着上一次的
    );
    return () => {
      gone = true;
    };
  }, [characterId, rewardedAt, unlockedAt]);

  // 换了角色、换了用户，或者别的标签页记下了看过：重新读这台设备上看过的。
  useEffect(() => {
    const reread = () => setSeen({ characterId, at: achievementsSeenUpTo(userId, characterId) });
    reread();
    window.addEventListener("storage", reread);
    return () => window.removeEventListener("storage", reread);
  }, [characterId, userId]);

  const her = read?.characterId === characterId ? read.her : null;
  const seenAt = seen.characterId === characterId ? seen.at : null;
  const latest = later(her ? latestUnlock(her) : null, unlockedAt);

  const keepSeen = useCallback(
    (at: string | null) => {
      if (at === null) return;
      keepAchievementsSeenUpTo(userId, characterId, at);
      setSeen({ characterId, at: achievementsSeenUpTo(userId, characterId) });
    },
    [userId, characterId],
  );

  const setPanelOpen = useCallback(
    (open: boolean) => {
      if (open) {
        setPanel({ isOpen: true, seenBefore: seenAt });
        keepSeen(latest);
      } else {
        setPanel((p) => ({ ...p, isOpen: false }));
      }
    },
    [seenAt, latest, keepSeen],
  );

  // 面板开着时又解锁了：那一行照样带点，也记成看过。
  useEffect(() => {
    if (panel.isOpen && isUnseen(latest, seenAt)) keepSeen(latest);
  }, [panel.isOpen, latest, seenAt, keepSeen]);

  return {
    her,
    isUnseen: her !== null && isUnseen(latest, seenAt),
    unseenRows: her ? unseenRows(her, panel.seenBefore) : NONE,
    isPanelOpen: panel.isOpen && her !== null,
    setPanelOpen,
  };
}

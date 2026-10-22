"use client";

import { useEffect, useState } from "react";

import { getScheduleNow, type ScheduleNow } from "@/lib/schedule";

const REFRESH_MS = 5 * 60_000;

/** 她现在大概在干嘛：打开她的对话页时读一次，之后每 5 分钟读一次；读不到就当没有。 */
export function useScheduleNow(characterId: string): ScheduleNow {
  const [now, setNow] = useState<{ characterId: string; now: ScheduleNow } | null>(null);
  useEffect(() => {
    let current = true;
    const read = () =>
      getScheduleNow(characterId).then(
        (found) => current && setNow({ characterId, now: found }),
        () => current && setNow({ characterId, now: null }),
      );
    void read();
    const timer = setInterval(() => void read(), REFRESH_MS);
    return () => {
      current = false;
      clearInterval(timer);
    };
  }, [characterId]);
  return now?.characterId === characterId ? now.now : null;
}

// 这台设备上「看过」到哪一刻：导航「礼物」上的点（lib/rewards）和成就的点（lib/achievements）都这样记。
// localStorage 里一个键存一个时刻（看过的最近一次到账、最近一次解锁）；读不出或者不是时刻的，算没看过；
// 这台设备上还没存过时，什么都算没看过。存不进去（无痕浏览）时只在这次页面里记着。

/** 存不进去时，这次页面里记着的，按存的键。 */
const seenThisPage = new Map<string, string>();

/** 存在 `key` 下的看过的最近时刻；没看过为 null。读不出或者不是时刻的算没看过，这次页面里记着的除外。 */
export function seenOnDevice(key: string): string | null {
  let stored: unknown = null;
  try {
    stored = JSON.parse(localStorage.getItem(key) ?? "null");
  } catch {
    // 读不出或者坏了：算没看过，这次页面里记着的除外。
  }
  const kept = typeof stored === "string" && !Number.isNaN(Date.parse(stored)) ? stored : null;
  return later(kept, seenThisPage.get(key) ?? null);
}

/** 在 `key` 下记下看过了 `at` 为止；别的标签页已经记下更晚的，就留着那个。存不进去时只在这次页面里记着。 */
export function keepSeenOnDevice(key: string, at: string): void {
  const seen = later(seenOnDevice(key), at) ?? at;
  seenThisPage.set(key, seen);
  try {
    localStorage.setItem(key, JSON.stringify(seen));
  } catch {
    // 存不进去：只在这次页面里记着。
  }
}

/** 时刻 `at` 算不算没看过：有这么一刻，而且晚于看过的（`seenAt`，没看过是 null）。比的是时刻，不是写法。 */
export function isUnseen(at: string | null, seenAt: string | null): boolean {
  if (at === null) return false;
  return seenAt === null || Date.parse(at) > Date.parse(seenAt);
}

/** 两个时刻里晚的那个；null 比哪个都早。 */
export function later(a: string | null, b: string | null): string | null {
  if (a === null) return b;
  if (b === null) return a;
  return Date.parse(b) > Date.parse(a) ? b : a;
}

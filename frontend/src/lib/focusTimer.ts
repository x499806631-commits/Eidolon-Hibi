// 番茄钟「一起专心」（对话页更多菜单里打开）：自己定专心多久，专心一段到时提醒休息 5 分钟，
// 休息完接着来，如此循环。这里只有时间怎么走、怎么暂停、剩下的怎么写、时长怎么收；
// 画界面、放提示音在 `components/conversations/FocusTimerBar.tsx` 和 `FocusSetup.tsx`。
//
// 时间按绝对时刻（毫秒时间戳）算，不按秒数累加：标签页被浏览器挂起、定时器被节流，回来时按
// 现在这一刻重算，计时条就不会比实际慢一截。挂起太久再回来只推进一段，不补算中间跳过的那些。

/** 没选过时长时用这个：分钟。 */
export const DEFAULT_MINUTES = 25;
/** 能填的最长、最短：分钟。 */
export const MIN_MINUTES = 1;
export const MAX_MINUTES = 120;
/** 休息一段多长，分钟：固定，不跟着专心一段变。 */
export const BREAK_MINUTES = 5;

const SECOND = 1000;
const STORE_KEY = "eidolon.focus.minutes";

/** 一段是专心还是休息。 */
export type Phase = "focus" | "break";

export type Timer = {
  phase: Phase;
  /** 专心一段的长度，分钟：从休息回到专心时按它算，也是用户选的那个。 */
  focus: number;
  /** 这一段的长度，秒。 */
  total: number;
  /** 这一段还剩多少秒，走完是 0。 */
  left: number;
  /** 走到这一刻（毫秒时间戳）为止；暂停时是 null。 */
  until: number | null;
};

/** 把用户填的分钟收进能用的范围：取整，太小太大按边界，不是数就用默认的。 */
export function clampMinutes(value: number): number {
  if (!Number.isFinite(value)) return DEFAULT_MINUTES;
  return Math.min(MAX_MINUTES, Math.max(MIN_MINUTES, Math.round(value)));
}

/** 从此刻起开一段专心，长度是 `minutes` 分钟（收不进范围的按边界算）。 */
export function start(at: number, minutes: number = DEFAULT_MINUTES): Timer {
  const focus = clampMinutes(minutes);
  const total = focus * 60;
  return { phase: "focus", focus, total, left: total, until: at + total * SECOND };
}

/** 走到 `at` 这一刻的样子：这一段走完就换成下一段，从头走。 */
export function tick(timer: Timer, at: number): Timer {
  if (timer.until === null) return timer;  // 暂停着，一秒也不走
  const left = Math.max(0, Math.ceil((timer.until - at) / SECOND));
  if (left > 0) return { ...timer, left };
  const phase: Phase = timer.phase === "focus" ? "break" : "focus";
  const total = (phase === "break" ? BREAK_MINUTES : timer.focus) * 60;
  return { ...timer, phase, total, left: total, until: at + total * SECOND };
}

/** 停在 `at` 这一刻，剩下的秒数留着。 */
export function pause(timer: Timer, at: number): Timer {
  if (timer.until === null) return timer;
  const left = Math.max(0, Math.ceil((timer.until - at) / SECOND));
  return { ...timer, left, until: null };
}

/** 从 `at` 这一刻接着走剩下的。 */
export function resume(timer: Timer, at: number): Timer {
  if (timer.until !== null) return timer;
  return { ...timer, until: at + timer.left * SECOND };
}

/** 剩余时间写成 `24:59` 这样，分钟不进位、不省略前导零。 */
export function formatLeft(seconds: number): string {
  const whole = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(whole / 60);
  return `${String(minutes).padStart(2, "0")}:${String(whole % 60).padStart(2, "0")}`;
}

/** 上次选的专心时长，分钟；没记过、记的不是数、太小、或读不到（隐私模式）就是默认的 25，
 * 记的太大按上限算。 */
export function loadMinutes(): number {
  try {
    const kept = Number(globalThis.localStorage?.getItem(STORE_KEY));
    if (!Number.isFinite(kept) || kept < MIN_MINUTES) return DEFAULT_MINUTES;
    return clampMinutes(kept);
  } catch {
    return DEFAULT_MINUTES;
  }
}

/** 记下这次选的专心时长；存不下就当没存，下次还是默认的。 */
export function rememberMinutes(minutes: number): void {
  try {
    globalThis.localStorage?.setItem(STORE_KEY, String(clampMinutes(minutes)));
  } catch {
    // 隐私模式、配额满了：记不住不影响这一轮计时
  }
}

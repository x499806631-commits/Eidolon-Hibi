// 来信（信箱页 `/letters`，GLOSSARY.md「来信」）：偶像写给制作人的信。后端只给已经送到的信，最新的在前；
// 打开一封就算读过（`POST /api/letters/{id}/read`）。导航「信箱」上的未读圆点由 `unreadLetters` 管：
// 页面打开时读一次，之后每分钟读一次，读了一封信马上减掉。

import { api, json, type Segment } from "@/lib/api";

/** 她为什么写这封信：平时、制作人的生日、她自己的生日、节日。 */
export type Occasion = "ordinary" | "producer_birthday" | "her_birthday" | "holiday";

export type Letter = {
  id: number;
  character_id: string;
  occasion: Occasion;
  ja: string;
  zh: string;
  /** 第一段是称呼，最后一段是落款，中间是正文。 */
  segments: Segment[];
  /** 送到的时间。 */
  created_at: string;
  /** 第一次打开的时间；没读过是 null。 */
  read_at: string | null;
};

export async function getLetters(): Promise<Letter[]> {
  return json(await api("/api/letters"));
}

export async function readLetter(id: number): Promise<Letter> {
  return json(await api(`/api/letters/${id}/read`, { method: "POST" }));
}

async function getUnread(): Promise<number> {
  const { unread } = await json<{ unread: number }>(await api("/api/letters/unread"));
  return unread;
}

/** 清单上一行露出的那句：正文的第一段（没有正文时是第一段）的中文。 */
export function preview(letter: Letter): string {
  const body = letter.segments.length > 2 ? letter.segments[1] : letter.segments[0];
  return (body?.zh ?? letter.zh).trim();
}

/** 信的三部分：称呼、正文、落款。只有一两段时没有正文以外的部分。 */
export function parts(letter: Letter): { greeting: Segment | null; body: Segment[]; signature: Segment | null } {
  const s = letter.segments;
  if (s.length < 3) return { greeting: null, body: s, signature: null };
  return { greeting: s[0], body: s.slice(1, -1), signature: s[s.length - 1] };
}

/** 送到的那天和时刻，按制作人的时区。 */
export function deliveredAt(letter: Letter, timeZone: string): { month: number; day: number; time: string } {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(letter.created_at));
  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return { month: Number(part("month")), day: Number(part("day")), time: `${part("hour")}:${part("minute")}` };
}

const POLL_MS = 60_000;

/** 未读的信有几封，给导航上的圆点用；有人订阅时每分钟读一次。读不到时当作 0。 */
export class UnreadLetters {
  private count = 0;
  private readonly listeners = new Set<() => void>();
  private timer: ReturnType<typeof setInterval> | null = null;

  constructor(private readonly fetchUnread: () => Promise<number> = getUnread) {}

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    if (this.listeners.size === 1) {
      void this.refresh();
      this.timer = setInterval(() => void this.refresh(), POLL_MS);
    }
    return () => {
      this.listeners.delete(listener);
      if (this.listeners.size === 0 && this.timer !== null) {
        clearInterval(this.timer);
        this.timer = null;
      }
    };
  };

  readonly snapshot = (): number => this.count;

  async refresh(): Promise<void> {
    try {
      this.set(await this.fetchUnread());
    } catch {
      // 读不到就先不变，下一分钟再读。
    }
  }

  /** 读了一封原来没读过的信。 */
  read(): void {
    this.set(Math.max(0, this.count - 1));
  }

  private set(count: number): void {
    if (count === this.count) return;
    this.count = count;
    for (const listener of this.listeners) listener();
  }
}

export const unreadLetters = new UnreadLetters();

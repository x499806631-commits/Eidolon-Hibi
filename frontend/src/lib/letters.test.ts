import { afterEach, describe, expect, it, vi } from "vitest";

import { deliveredAt, parts, preview, UnreadLetters, type Letter } from "@/lib/letters";

function letter(zh: string[], at = "2026-10-20T22:00:00+00:00"): Letter {
  const segments = zh.map((z) => ({ ja: `「${z}」`, zh: z }));
  return {
    id: 1,
    character_id: "mano",
    occasion: "ordinary",
    ja: segments.map((s) => s.ja).join(""),
    zh: zh.join(""),
    segments,
    created_at: at,
    read_at: null,
    keepsake: null,
  };
}

afterEach(() => vi.useRealTimers());

describe("一封信", () => {
  it("第一段是称呼，最后一段是落款，清单上露出正文的第一段", () => {
    const l = letter(["致制作人", "最近变冷了。", "要注意身体。", "真乃"]);
    expect(parts(l)).toMatchObject({
      greeting: { zh: "致制作人" },
      body: [{ zh: "最近变冷了。" }, { zh: "要注意身体。" }],
      signature: { zh: "真乃" },
    });
    expect(preview(l)).toBe("最近变冷了。");
  });

  it("只有一两段时都算正文", () => {
    const l = letter(["圣诞快乐！", "真乃"]);
    expect(parts(l)).toEqual({ greeting: null, body: l.segments, signature: null });
    expect(preview(l)).toBe("圣诞快乐！");
  });

  it("送到的日子和时刻按制作人的时区", () => {
    expect(deliveredAt(letter(["x"]), "Asia/Shanghai")).toEqual({ month: 10, day: 21, time: "06:00" });
    expect(deliveredAt(letter(["x"]), "Europe/Madrid")).toEqual({ month: 10, day: 21, time: "00:00" });
  });
});

describe("未读的信", () => {
  it("有人订阅时读一次，之后每分钟一次；读了一封马上减掉；没人订阅就不读", async () => {
    vi.useFakeTimers();
    const fetches = [2, 3];
    const fetchUnread = vi.fn(async () => fetches.shift() ?? 0);
    const unread = new UnreadLetters(fetchUnread);
    const seen: number[] = [];

    const stop = unread.subscribe(() => seen.push(unread.snapshot()));
    await vi.advanceTimersByTimeAsync(0);
    unread.read();
    await vi.advanceTimersByTimeAsync(60_000);
    stop();
    await vi.advanceTimersByTimeAsync(120_000);

    expect(seen).toEqual([2, 1, 3]);
    expect(fetchUnread).toHaveBeenCalledTimes(2);
  });
});

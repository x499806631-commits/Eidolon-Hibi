import { afterEach, describe, expect, it, vi } from "vitest";

import { hasNewRewards, keepRewardsSeenUpTo, rewardsSeenUpTo } from "@/lib/rewards";

/** This browser's localStorage, kept in memory. */
function storage(initial: Record<string, string> = {}) {
  const items = new Map(Object.entries(initial));
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => items.get(key) ?? null,
    setItem: (key: string, value: string) => void items.set(key, value),
    removeItem: (key: string) => void items.delete(key),
  });
  return items;
}

afterEach(() => vi.unstubAllGlobals());

describe("what rewards have been seen on this device", () => {
  it("is kept for each user apart, and read back as kept", () => {
    const items = storage();

    keepRewardsSeenUpTo("seer-1", "2026-10-20T08:00:00+00:00");
    keepRewardsSeenUpTo("seer-2", "2026-10-19T08:00:00+00:00");

    expect(rewardsSeenUpTo("seer-1")).toBe("2026-10-20T08:00:00+00:00");
    expect(rewardsSeenUpTo("seer-2")).toBe("2026-10-19T08:00:00+00:00");
    expect(rewardsSeenUpTo("seer-3")).toBeNull();
    expect(items.get("eidolon.rewardsSeen.seer-1")).toBe(JSON.stringify("2026-10-20T08:00:00+00:00"));
  });

  it("is nothing when what is stored is not a time", () => {
    storage({
      "eidolon.rewardsSeen.broken-1": "{not json",
      "eidolon.rewardsSeen.broken-2": JSON.stringify(42),
      "eidolon.rewardsSeen.broken-3": JSON.stringify("yesterday"),
    });

    expect(rewardsSeenUpTo("broken-1")).toBeNull();
    expect(rewardsSeenUpTo("broken-2")).toBeNull();
    expect(rewardsSeenUpTo("broken-3")).toBeNull();
  });

  it("keeps the later of what another tab kept and what is kept now", () => {
    storage({ "eidolon.rewardsSeen.tabs-1": JSON.stringify("2026-10-20T09:00:00+00:00") });

    keepRewardsSeenUpTo("tabs-1", "2026-10-20T08:00:00+00:00");

    expect(rewardsSeenUpTo("tabs-1")).toBe("2026-10-20T09:00:00+00:00");
  });

  it("stays for this page when storage cannot be used", () => {
    const unreachable = () => {
      throw new DOMException("denied", "SecurityError");
    };
    vi.stubGlobal("localStorage", { getItem: unreachable, setItem: unreachable, removeItem: unreachable });

    expect(rewardsSeenUpTo("private-1")).toBeNull();
    expect(() => keepRewardsSeenUpTo("private-1", "2026-10-20T08:00:00+00:00")).not.toThrow();
    expect(rewardsSeenUpTo("private-1")).toBe("2026-10-20T08:00:00+00:00");
  });
});

describe("the dot on 「礼物」", () => {
  const at = "2026-10-20T08:00:00+00:00";

  it("is lit by any reward when none has been seen on this device", () => {
    expect(hasNewRewards({ isOpen: true, rewardedAt: at, seenAt: null })).toBe(true);
  });

  it("is not lit when there has never been a reward", () => {
    expect(hasNewRewards({ isOpen: true, rewardedAt: null, seenAt: null })).toBe(false);
  });

  it("is not lit once the latest reward has been seen", () => {
    expect(hasNewRewards({ isOpen: true, rewardedAt: at, seenAt: at })).toBe(false);
  });

  it("is lit again by a reward later than the one seen", () => {
    expect(hasNewRewards({ isOpen: true, rewardedAt: "2026-10-20T09:00:00+00:00", seenAt: at })).toBe(true);
  });

  it("compares the times, not how they are written", () => {
    expect(hasNewRewards({ isOpen: true, rewardedAt: "2026-10-20T16:00:00+08:00", seenAt: at })).toBe(false);
  });

  it("is not lit while gifts are closed", () => {
    expect(hasNewRewards({ isOpen: false, rewardedAt: at, seenAt: null })).toBe(false);
  });
});

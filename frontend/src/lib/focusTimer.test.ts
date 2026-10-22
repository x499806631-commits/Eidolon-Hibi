import { afterEach, describe, expect, it, vi } from "vitest";

import {
  BREAK_MINUTES,
  clampMinutes,
  DEFAULT_MINUTES,
  formatLeft,
  loadMinutes,
  MAX_MINUTES,
  MIN_MINUTES,
  pause,
  rememberMinutes,
  resume,
  start,
  tick,
} from "@/lib/focusTimer";

/** 2026-10-21 09:00 的那一刻，假时钟从这里开始走。 */
const NOW = Date.UTC(2026, 9, 21, 1, 0, 0);

function at(seconds: number): number {
  return Date.now() + seconds * 1000;
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function timing() {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
}

describe("番茄钟的走法", () => {
  it("按选的分钟数开始，按假时钟一秒一秒往下数", () => {
    timing();
    let timer = start(Date.now(), 25);
    expect(timer).toMatchObject({ phase: "focus", focus: 25, total: 25 * 60, left: 1500 });

    vi.advanceTimersByTime(60_000);
    timer = tick(timer, Date.now());
    expect(timer.left).toBe(1440);  // 走了一分钟，还剩 24 分钟
    expect(timer.phase).toBe("focus");
  });

  it("专心走完换成 5 分钟休息，休息走完又回到选的那段时长", () => {
    timing();
    // 选的是 15 分钟：休息还是 5 分钟，休息完回到 15 分钟
    let timer = tick(start(Date.now(), 15), at(15 * 60));

    expect(timer).toMatchObject({ phase: "break", focus: 15, total: BREAK_MINUTES * 60, left: 300 });

    timer = tick(timer, at((15 + BREAK_MINUTES) * 60));
    expect(timer).toMatchObject({ phase: "focus", focus: 15, total: 15 * 60, left: 15 * 60 });
  });

  it("暂停时一秒也不走，继续时接着剩下的走", () => {
    timing();
    let timer = start(Date.now(), 30);
    vi.advanceTimersByTime(120_000);
    timer = pause(timer, Date.now());
    expect(timer).toMatchObject({ left: 1680, until: null });

    vi.advanceTimersByTime(600_000);  // 暂停着过了十分钟
    expect(tick(timer, Date.now())).toBe(timer);  // 一动不动

    timer = resume(timer, Date.now());
    vi.advanceTimersByTime(30_000);
    expect(tick(timer, Date.now()).left).toBe(1650);
  });

  it("挂起很久再回来只推进一段，不把中间跳过的补算成一串", () => {
    timing();
    const timer = start(Date.now(), DEFAULT_MINUTES);

    // 标签页被挂起两个小时（好几个来回），回来时只换到休息，并从头走
    const late = tick(timer, at(120 * 60));
    expect(late).toMatchObject({ phase: "break", left: BREAK_MINUTES * 60 });
    expect(tick(late, at(120 * 60))).toEqual(late);  // 同一刻再 tick 原地不动，没有连跳
  });
});

describe("选的分钟数", () => {
  it("取整并收进 1 到 120 之间", () => {
    expect(clampMinutes(45)).toBe(45);
    expect(clampMinutes(45.4)).toBe(45);
    expect(clampMinutes(0)).toBe(MIN_MINUTES);
    expect(clampMinutes(-10)).toBe(MIN_MINUTES);
    expect(clampMinutes(999)).toBe(MAX_MINUTES);
  });

  it("不是数就用默认的 25 分钟", () => {
    expect(clampMinutes(Number.NaN)).toBe(DEFAULT_MINUTES);
    expect(clampMinutes(Number.POSITIVE_INFINITY)).toBe(DEFAULT_MINUTES);
    expect(start(NOW, Number.NaN).focus).toBe(DEFAULT_MINUTES);
  });

  it("开始时就收进范围，不用等到最后一段", () => {
    expect(start(NOW, 999)).toMatchObject({ focus: MAX_MINUTES, total: MAX_MINUTES * 60 });
    expect(start(NOW)).toMatchObject({ focus: DEFAULT_MINUTES });
  });
});

describe("剩余时间怎么写", () => {
  it("写成 mm:ss，前导零不省", () => {
    expect(formatLeft(1500)).toBe("25:00");
    expect(formatLeft(59)).toBe("00:59");
    expect(formatLeft(0)).toBe("00:00");
    expect(formatLeft(61)).toBe("01:01");
  });

  it("负数按 0 写，不写出负号", () => {
    expect(formatLeft(-3)).toBe("00:00");
  });
});

describe("记下的专心时长", () => {
  function storing(value: string | null) {
    vi.stubGlobal("localStorage", { getItem: () => value, setItem: () => {} });
  }

  it("读回存下的分钟数；没记过、记的不是数、太小的都用默认的 25", () => {
    storing("40");
    expect(loadMinutes()).toBe(40);
    storing("120");
    expect(loadMinutes()).toBe(120);
    for (const value of [null, "", "abc", "0", "-5"]) {
      storing(value);
      expect(loadMinutes(), String(value)).toBe(DEFAULT_MINUTES);
    }
  });

  it("存得太大按上限算，不当作没设过", () => {
    storing("999");
    expect(loadMinutes()).toBe(MAX_MINUTES);
  });

  it("存的时候收进范围再写进 localStorage", () => {
    const written: string[] = [];
    vi.stubGlobal("localStorage", { getItem: () => null, setItem: (_: string, v: string) => written.push(v) });
    rememberMinutes(30.6);
    rememberMinutes(9999);
    expect(written).toEqual(["31", "120"]);
  });

  it("localStorage 读写抛错时退回默认，不把界面带崩", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("privacy mode");
      },
      setItem: () => {
        throw new Error("quota");
      },
    });
    expect(loadMinutes()).toBe(DEFAULT_MINUTES);
    expect(() => rememberMinutes(30)).not.toThrow();
  });
});

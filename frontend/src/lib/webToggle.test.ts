import { afterEach, describe, expect, it, vi } from "vitest";

import { isWebOn, rememberWeb } from "@/lib/webToggle";

afterEach(() => {
  vi.unstubAllGlobals();
});

function storing(read: (key: string) => string | null, write: (key: string, value: string) => void = () => {}) {
  vi.stubGlobal("localStorage", { getItem: read, setItem: write });
}

describe("联网的开关", () => {
  it("没拨过就是关", () => {
    storing(() => null);
    expect(isWebOn("tenka")).toBe(false);
  });

  it("按角色分开记，各是各的", () => {
    const kept = new Map<string, string>();
    storing(
      (key) => kept.get(key) ?? null,
      (key: string, value: string) => void kept.set(key, value),
    );

    rememberWeb("tenka", true);

    expect(isWebOn("tenka")).toBe(true);
    expect(isWebOn("mano")).toBe(false);

    rememberWeb("tenka", false);
    expect(isWebOn("tenka")).toBe(false);
  });

  it("读不到 localStorage 时当作关，也不抛错", () => {
    vi.stubGlobal("localStorage", {
      getItem: () => {
        throw new Error("privacy mode");
      },
      setItem: () => {
        throw new Error("quota");
      },
    });

    expect(isWebOn("tenka")).toBe(false);
    expect(() => rememberWeb("tenka", true)).not.toThrow();
  });
});

import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  accentDelay,
  Ambience,
  AMBIENCE_SCENES,
  AUTO,
  loadAmbience,
  sceneForKind,
  type AmbienceKind,
} from "@/lib/ambience";

const FOLDER = fileURLToPath(new URL("../../public/ambience/", import.meta.url));

function storing(value: string | null) {
  vi.stubGlobal("localStorage", { getItem: () => value, setItem: () => {} });
}

afterEach(() => vi.unstubAllGlobals());

describe("环境音的场景", () => {
  it("用到的每个文件都在 public/ambience 里", () => {
    const files = AMBIENCE_SCENES.flatMap((s) => [...s.beds.map((b) => b.file), ...(s.accent ? [s.accent.file] : [])]);
    expect(files.filter((f) => !existsSync(FOLDER + f))).toEqual([]);
  });

  it("每个场景的标识都不一样", () => {
    const ids = AMBIENCE_SCENES.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("记下的选择", () => {
  it("读回存下的场景和音量，关掉的也是一种选择", () => {
    storing(JSON.stringify({ scene: "waves-2", volume: "loud" }));
    expect(loadAmbience()).toEqual({ scene: "waves-2", volume: "loud" });
    storing(JSON.stringify({ scene: null, volume: "quiet" }));
    expect(loadAmbience()).toEqual({ scene: null, volume: "quiet" });
    storing(JSON.stringify({ scene: AUTO, volume: "medium" }));
    expect(loadAmbience()).toEqual({ scene: AUTO, volume: "medium" });
  });

  it("没存过、存的场景已经没有了、或者读不出来时用默认的", () => {
    storing(null);
    const defaults = loadAmbience();
    storing(JSON.stringify({ scene: "gone", volume: "deafening" }));
    expect(loadAmbience()).toEqual(defaults);
    vi.stubGlobal("localStorage", undefined);
    expect(loadAmbience()).toEqual(defaults);
  });
});

describe("点缀隔多久响一次", () => {
  it("在给的范围里", () => {
    expect(accentDelay([40, 90], 0)).toBe(40);
    expect(accentDelay([40, 90], 0.5)).toBe(65);
    expect(accentDelay([40, 90], 0.999)).toBeLessThan(90);
  });
});

describe("判断模型挑的那一类", () => {
  const kinds: AmbienceKind[] = ["rain", "storm", "fireplace", "waves", "summer-night", "study", "cafe"];

  it("后端会挑的每一类都有场景", () => {
    for (const kind of kinds) expect(sceneForKind(kind, null, 0)).toBeTruthy();
  });

  it("安静是停下，同一类接着放，换了类随机放一个", () => {
    expect(sceneForKind("none", "rain-1", 0)).toBeNull();
    expect(sceneForKind("rain", "rain-2", 0)).toBeUndefined();
    expect(sceneForKind("rain", "cafe", 0)).toBe("rain-1");
    expect(sceneForKind("rain", null, 0.99)).toBe("rain-2");
  });
});

function silentContext(): AudioContext {
  const param = () => ({
    value: 1,
    cancelScheduledValues: () => {},
    setValueAtTime: () => {},
    linearRampToValueAtTime: () => {},
  });
  return {
    state: "running",
    currentTime: 0,
    destination: {},
    createGain: () => ({ gain: param(), connect: () => {}, disconnect: () => {} }),
    resume: async () => {},
  } as unknown as AudioContext;
}

function ambienceStoring(scene: string | null) {
  vi.stubGlobal("localStorage", {
    getItem: () => JSON.stringify({ scene, volume: "medium" }),
    setItem: () => {},
  });
  vi.stubGlobal("fetch", () => Promise.reject(new Error("没有文件")));
  const context = silentContext();
  return new Ambience(() => context, "/ambience/", () => 0.99);
}

describe("自动", () => {
  it("ASMR 通话接通后请后端挑，按挑的放；换成别的场景就请它别挑了", () => {
    const ambience = ambienceStoring(AUTO);
    const told: boolean[] = [];

    ambience.callStarted(true);
    expect(ambience.snapshot()).toMatchObject({ auto: true, playing: null });
    ambience.connected((on) => told.push(on));
    expect(told).toEqual([true]);

    ambience.picked("rain");
    expect(ambience.snapshot().playing).toBe("rain-2");
    ambience.picked("rain");
    expect(ambience.snapshot().playing).toBe("rain-2");
    ambience.picked("fireplace");
    expect(ambience.snapshot().playing).toBe("fireplace-2");
    ambience.picked("none");
    expect(ambience.snapshot()).toMatchObject({ playing: null, picked: "none" });

    ambience.choose("cafe");
    expect(told).toEqual([true, false]);
    expect(ambience.snapshot()).toMatchObject({ auto: false, playing: "cafe", scene: "cafe" });

    ambience.callEnded();
    expect(ambience.snapshot()).toMatchObject({ auto: false, playing: null });
  });

  it("普通通话里先不挑，点了「自动」才请后端挑；在放的接着放，直到挑出来", () => {
    const ambience = ambienceStoring("waves-1");
    const told: boolean[] = [];

    ambience.callStarted(false);
    ambience.connected((on) => told.push(on));
    expect(told).toEqual([]);
    ambience.choose("waves-1");
    expect(ambience.snapshot().playing).toBe("waves-1");

    ambience.choose(AUTO);
    expect(told).toEqual([true]);
    expect(ambience.snapshot()).toMatchObject({ auto: true, playing: "waves-1" });
    ambience.picked("waves");
    expect(ambience.snapshot().playing).toBe("waves-1");
  });

  it("挂断以后后端发来的不再放", () => {
    const ambience = ambienceStoring(AUTO);
    ambience.callStarted(true);
    ambience.callEnded();

    ambience.picked("cafe");

    expect(ambience.snapshot().playing).toBeNull();
  });
});

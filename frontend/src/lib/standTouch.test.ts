import { describe, expect, it } from "vitest";

import { isPestering, partAt, reactionTo } from "./standTouch";

const body = { head: { x: 0, y: 100 }, radius: 10, hands: [{ x: 30, y: 40 }] };

describe("partAt", () => {
  it("头的上半是头、下半是脸颊，手在手骨旁边，肩膀在头下面两侧，别处没有", () => {
    expect(partAt({ x: 0, y: 105 }, body)).toBe("head");
    expect(partAt({ x: 4, y: 94 }, body)).toBe("cheek");
    expect(partAt({ x: 32, y: 42 }, body)).toBe("hand");
    expect(partAt({ x: 15, y: 80 }, body)).toBe("shoulder");
    expect(partAt({ x: 0, y: 50 }, body)).toBeNull();
    expect(partAt({ x: 60, y: 100 }, body)).toBeNull();
  });
});

describe("isPestering", () => {
  it("6 秒里点到第 4 下算在闹她", () => {
    expect(isPestering([0, 1000, 2000], 2500)).toBe(false);
    expect(isPestering([0, 1000, 2000, 2500], 2500)).toBe(true);
    expect(isPestering([0, 1000, 8000, 8500], 8500)).toBe(false);
  });
});

describe("reactionTo", () => {
  const has = (names: string[]) => (name: string) => names.includes(name);

  it("摸头先用衣装自带的 touch，没有就害羞；有脸红就脸红", () => {
    expect(reactionTo("head", false, has(["touch", "face_shy", "cheek_red"]))).toEqual({
      body: "touch",
      face: "face_shy",
      blush: true,
    });
    expect(reactionTo("head", false, has(["shy1", "face_smile"]))).toEqual({ body: "shy1", face: "face_smile", blush: false });
  });

  it("一直戳她就生气，不脸红", () => {
    expect(reactionTo("cheek", true, has(["anger1", "face_anger", "cheek_red"]))).toEqual({
      body: "anger1",
      face: "face_anger",
      blush: false,
    });
  });
});

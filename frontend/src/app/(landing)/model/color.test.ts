import { describe, expect, it } from "vitest";

import { contrast } from "@/lib/imageColor";

import { idolColors } from "./color";

/** 28 位偶像和事务员はづき的官方印象色（characters/characters.toml）。 */
const OFFICIALS = {
  真乃: "#ffbad6",
  灯織: "#144384",
  めぐる: "#ffe012",
  恋鐘: "#f84cad",
  摩美々: "#a846fb",
  咲耶: "#006047",
  結華: "#3b91c4",
  霧子: "#d9f2ff",
  果穂: "#e5461c",
  智代子: "#f93b90",
  樹里: "#ffc602",
  凛世: "#89c3eb",
  夏葉: "#90e667",
  甘奈: "#f54275",
  甜花: "#e75bec",
  千雪: "#fbfafa",
  あさひ: "#f30100",
  冬優子: "#5ce626",
  愛依: "#ff00ff",
  透: "#50d0d0",
  円香: "#be1e3e",
  小糸: "#7967c3",
  雛菜: "#ffc639",
  にちか: "#a6ceb6",
  美琴: "#760f10",
  ルカ: "#24130d",
  羽那: "#e0b5d3",
  はるき: "#ead7a4",
  はづき: "#c3d825",
};

/** 官方色，加上任意色相、任意深浅的颜色：以后的新角色也要守住同样的契约。 */
function everyColor() {
  const colors = Object.entries(OFFICIALS).map(([name, hex]) => ({ name: `${name} ${hex}`, hex }));
  for (let r = 0; r < 256; r += 51)
    for (let g = 0; g < 256; g += 51)
      for (let b = 0; b < 256; b += 51) {
        const hex = `#${[r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("")}`;
        colors.push({ name: hex, hex });
      }
  return colors;
}

const BLACK = "#000000";
/** 光晕在黑底上至少要有的对比度：大约是 OKLab 明度 0.66 的灰。 */
const HALO_ON_BLACK = 6;

describe("idolColors: 每位偶像在落地页上的一组颜色", () => {
  it("官方色原样不改，只统一成小写", () => {
    expect(idolColors("#144384").official).toBe("#144384");
    expect(idolColors("#FFBAD6").official).toBe("#ffbad6");
  });

  it("每一种颜色的光晕在黑底上都看得见，深蓝的灯織也是", () => {
    const failures = everyColor()
      .map(({ name, hex }) => ({ name, ratio: contrast(idolColors(hex).halo, BLACK) }))
      .filter(({ ratio }) => ratio < HALO_ON_BLACK)
      .map(({ name, ratio }) => `${name} ${ratio.toFixed(2)}`);
    expect(failures).toEqual([]);
  });

  it("越暗的颜色光晕晕得越开，越亮的光晕压得越低", () => {
    const grays = ["#101010", "#404040", "#808080", "#c0c0c0", "#ffffff"].map(idolColors);
    for (let i = 1; i < grays.length; i++) {
      expect(grays[i].haloSpread).toBeLessThanOrEqual(grays[i - 1].haloSpread);
      expect(grays[i].haloGain).toBeLessThanOrEqual(grays[i - 1].haloGain);
    }
    expect(idolColors(OFFICIALS.灯織).haloSpread).toBeGreaterThan(idolColors(OFFICIALS.真乃).haloSpread);
    expect(idolColors(OFFICIALS.千雪).haloGain).toBeLessThan(idolColors(OFFICIALS.灯織).haloGain);
  });

  it("官方色上的字、黑底和她的极深底色上的名字都读得清", () => {
    const failures: string[] = [];
    for (const { name, hex } of everyColor()) {
      const c = idolColors(hex);
      const pairs = [
        { what: "官方色上的字", fg: c.onOfficial, bg: c.official },
        { what: "黑底上的名字", fg: c.ink, bg: BLACK },
        { what: "极深底色上的名字", fg: c.ink, bg: c.deep },
      ];
      for (const p of pairs) {
        const ratio = contrast(p.fg, p.bg);
        if (ratio < 4.5) failures.push(`${name} ${p.what} ${p.fg} on ${p.bg} ${ratio.toFixed(2)}`);
      }
    }
    expect(failures).toEqual([]);
  });

  it("极深底色接近黑，光在上面仍然亮得起来", () => {
    for (const hex of Object.values(OFFICIALS)) expect(contrast(idolColors(hex).deep, BLACK)).toBeLessThan(1.5);
  });
});

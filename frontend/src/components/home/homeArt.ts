// 制作人的家的插画用色（GLOSSARY.md「制作人的家」）。屋子是一张画，同礼物插画、她发来的照片一样是内容，
// 不是界面：它的颜色写在这里，不走主题变量，也不跟深浅色模式变；屋里的光跟着一天里的时候变。
// 界面上的东西（页首、按钮、提示条、相册）照常用主题。

import type { Room, Sky, TimeOfDay } from "@/lib/home";

export type RoomPalette = {
  wall: string;
  wallShade: string;
  trim: string;
  floor: string;
  floorLine: string;
  rug: string;
  rugEdge: string;
  wood: string;
  woodDark: string;
  woodLight: string;
  fabric: string;
  fabricShade: string;
  accent: string;
  metal: string;
  paper: string;
  ink: string;
  glass: string;
};

export const PALETTES: Record<Room, RoomPalette> = {
  apartment: {
    wall: "#f4e9dc",
    wallShade: "#e8d9c6",
    trim: "#d9c3a5",
    floor: "#c99a6b",
    floorLine: "#b7865a",
    rug: "#f2c4c0",
    rugEdge: "#e3a9a4",
    wood: "#b5835a",
    woodDark: "#8c6142",
    woodLight: "#d4a77c",
    fabric: "#8fb3d9",
    fabricShade: "#7399c2",
    accent: "#e2665c",
    metal: "#9aa3ad",
    paper: "#fffaf2",
    ink: "#5b4636",
    glass: "#cfe3f2",
  },
  mansion: {
    wall: "#e7dfee",
    wallShade: "#d6cae0",
    trim: "#f6f1e7",
    floor: "#7d5638",
    floorLine: "#6a472d",
    rug: "#9b3f4b",
    rugEdge: "#c99a4a",
    wood: "#6e4630",
    woodDark: "#4d2f20",
    woodLight: "#8f5f42",
    fabric: "#5c4a8a",
    fabricShade: "#4a3a73",
    accent: "#c99a4a",
    metal: "#d8b46a",
    paper: "#fffaf0",
    ink: "#3f2e2a",
    glass: "#d6e6f3",
  },
};

/** 屋里罩一层的光：颜色和浓淡。白天没有。 */
export const LIGHT: Record<TimeOfDay, { color: string; opacity: number }> = {
  morning: { color: "#ffd8a3", opacity: 0.12 },
  day: { color: "#ffffff", opacity: 0 },
  evening: { color: "#ff8f4f", opacity: 0.2 },
  night: { color: "#1c2452", opacity: 0.5 },
};

/** 窗外的天，从上到下两色。阴雨雾雪的白天发灰。 */
export function skyColors(time: TimeOfDay, sky: Sky | null): [string, string] {
  const grey = sky !== null && sky !== "clear";
  switch (time) {
    case "morning":
      return grey ? ["#b9c3cf", "#e6dcd3"] : ["#9cc9f0", "#ffe1c2"];
    case "day":
      return grey ? ["#a9b4c0", "#d5dce3"] : ["#6fb3ec", "#cfe8fb"];
    case "evening":
      return grey ? ["#8f8597", "#d1a48f"] : ["#f08a5d", "#ffd39a"];
    case "night":
      return grey ? ["#1d2236", "#343a55"] : ["#0f1636", "#2d3a6e"];
  }
}

/** 夜里亮着的灯光颜色。 */
export const LAMP_GLOW = "#ffd58a";
/** 有新东西时的小圆点、手机亮起来的光。 */
export const NOTICE = "#ff5a5f";
export const SCREEN_GLOW = "#9fe3ff";
export const CLOUD = "#ffffff";
export const RAIN = "#dfe9f5";
export const STAR = "#fff6d5";
export const ENVELOPE = "#fffdf7";
export const DIARY_COVER = "#f3a6b8";
export const DIARY_SPINE = "#d97f97";
export const KEY_GOLD = "#e8c25a";
export const RIBBON = "#e2665c";
export const PHONE_BODY = "#2b2b35";
export const PLACEHOLDER = "#e9e2d8";

// 落地页的字体子集要覆盖的文字（ADR-0016 修订）：落地页和登录组件的文案、所有角色名、组合名、
// 招呼和开场白的原文和字幕。子集脚本（scripts/landing-fonts.mts）按它生成子集，覆盖契约测试拿它和子集的
// 清单比对。这里读仓库里的角色配置和世界设定，只在本地（脚本和测试）运行，不进页面：前端的
// Docker 构建拿不到角色配置。

import { readFileSync } from "node:fs";

import { copy } from "@/i18n";
import { PROVIDER_LABELS } from "@/lib/signIn";

const CHARACTERS = new URL("../../../../../characters/", import.meta.url);

/** 不管文案里有没有，总要有的字：ASCII（偶像编号、色值、拉丁字母的组合名），和竖排里常见的标点。 */
const ALWAYS =
  Array.from({ length: 0x7f - 0x21 }, (_, i) => String.fromCharCode(0x21 + i)).join("") +
  "、。，．・：；！？「」『』（）〈〉《》【】〔〕ー～…‥—―‘’“”♪☆★";

/** 文案目录里的一节：字符串原样收；带参数的句子用空参数调一次，收它的固定部分，参数另外收。 */
function strings(node: unknown): string[] {
  if (typeof node === "string") return [node];
  if (typeof node === "function") return strings((node as (...args: string[]) => unknown)("", "", ""));
  if (node && typeof node === "object") return Object.values(node).flatMap(strings);
  return [];
}

/** 角色配置里名字是 `key` 的每一项的值。只认单行的普通字符串，配置里的写法都是这样。 */
function configured(key: string): string[] {
  const toml = readFileSync(new URL("characters.toml", CHARACTERS), "utf-8");
  const line = new RegExp(String.raw`^\s*(?:${key})\s*=\s*"((?:[^"\\]|\\.)*)"`, "gm");
  return [...toml.matchAll(line)].map(([, value]) => JSON.parse(`"${value}"`) as string);
}

/** 每位角色的显示名。 */
const characterNames = () => configured("name");

/** 语气提示：方括号里的英文（`[softly]`），照抄后端 delivery_cues.py 的 `_CUE`。只给语音合成听，页面上不显示。
 * 后端还会去掉括号里的旁白（`（笑）`），这里不去：收的字只多不少，子集不会缺字。 */
const DELIVERY_CUE = /\[[A-Za-z][A-Za-z ,'\-]*\]/g;

/** 招呼的日语原文（角色配置的 greeting），去掉语气提示；比角色列表接口给的 `greeting.ja` 只多不少。 */
const greetingJapanese = () => configured("greeting").map((text) => text.replace(DELIVERY_CUE, ""));

/** 招呼的中文字幕（角色配置的 greeting_subtitle）。 */
const greetingSubtitles = () => configured("greeting_subtitle");

/** 开场白的日语原文（角色配置的 call_opener），去掉语气提示；第 4 幕接通后显示。 */
const callOpenerJapanese = () => configured("call_opener").map((text) => text.replace(DELIVERY_CUE, ""));

/** 开场白的中文字幕（角色配置的 call_opener_subtitle）。 */
const callOpenerSubtitles = () => configured("call_opener_subtitle");

/** 世界设定里的组合名；没有资料文件时没有。 */
function unitNames(): string[] {
  try {
    const world = JSON.parse(readFileSync(new URL("world.json", CHARACTERS), "utf-8"));
    return world.units.map((u: { name: string }) => u.name);
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code === "ENOENT") return [];
    throw e;
  }
}

/** 去重、排好序，不含空白。 */
const unique = (parts: string[]) => [...new Set(parts.join("").replace(/\s/g, ""))].sort().join("");

/** 要覆盖的每一个字。 */
export function landingText(): string {
  return unique([
    ALWAYS,
    ...strings(copy.app),
    ...strings(copy.landing),
    ...strings(copy.legal),
    ...strings(copy.login),
    ...Object.values(PROVIDER_LABELS),
    ...characterNames(),
    ...greetingJapanese(),
    ...greetingSubtitles(),
    ...callOpenerJapanese(),
    ...callOpenerSubtitles(),
    ...unitNames(),
  ]);
}

/** 用 Shippori Mincho B1 排的：偶像的名字、组合名（没有组合的那一段的「283プロ」也排在组合名的位置）、招呼的日语原文、
 * 幕名和幕名轨，以及第 3、4、5 幕里她们说的日语（第 4 幕是开场白，第 5 幕是气泡里的原文）。 */
export function japaneseText(): string {
  const { act1, act2, act3, act5, rail } = copy.landing;
  return unique([
    ...characterNames(),
    ...unitNames(),
    ...greetingJapanese(),
    act1.name,
    ...rail.acts,
    act2.others,
    ...act3.lines.map((l) => l.ja),
    ...callOpenerJapanese(),
    ...act5.lines.map((l) => l.ja),
  ]);
}

/**
 * 第 0 幕（首屏）上的字：标题、副标题、首屏声明和右上角的仓库链接、入口（Landing.tsx）。这几个字另做一份很小的子集，
 * 只预载它：完整子集一起预载会和首屏的 CSS 抢带宽，手机 4G 下首屏晚 0.5 秒。第 0 幕的文案改了而没有
 * 重新生成时，那几个字会从完整子集里取，字体对、只是晚一点到，覆盖契约测试同样会拦。
 */
export function act0Text(): string {
  return unique([copy.app.name, copy.app.description, copy.landing.disclaimer, copy.app.repositoryLabel, copy.landing.signIn, copy.landing.continue]);
}

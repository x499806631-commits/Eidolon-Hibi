// 「联网」的开关（GLOSSARY.md「联网」）：按角色记在这台设备上，和「一起专心」的时长、
// 换衣装记的那套一样（localStorage，读写都包 try/catch）。拨开之后她那边的回合会带上
// `web: true`，后端才把搜索工具给她；关掉时前端一个字节也不多发，她的回合和以前一样。

const STORE_PREFIX = "eidolon.web.";

/** 这位偶像的联网开着没有；没拨过、读不到（隐私模式）都是关。 */
export function isWebOn(characterId: string): boolean {
  try {
    return globalThis.localStorage?.getItem(STORE_PREFIX + characterId) === "1";
  } catch {
    return false;
  }
}

/** 记下这位偶像的开关；存不下就只在这一页有效。 */
export function rememberWeb(characterId: string, on: boolean): void {
  try {
    globalThis.localStorage?.setItem(STORE_PREFIX + characterId, on ? "1" : "0");
  } catch {
    // 隐私模式、配额满了：记不住不影响这一轮的对话
  }
}

// 摸摸立绘（FORK.md「立绘触摸」）：点在她身上哪儿、她怎么反应。只认头、脸颊、手和肩膀，别的地方点了没反应。
// 位置按骨骼的实时位置算（找不到骨骼时按整个人的范围估），动画只从这套衣装实际有的里挑。

export type TouchPart = "head" | "cheek" | "hand" | "shoulder";

export type Point = { x: number; y: number };

/** 她身上能摸的几处，在骨骼的世界坐标里（y 朝上）：头的中心和半径，两只手。 */
export type Body = { head: Point; radius: number; hands: Point[] };

/** 点在 `at`（世界坐标）上碰到了哪儿；都没碰到是 null。 */
export function partAt(at: Point, { head, radius: r, hands }: Body): TouchPart | null {
  for (const hand of hands) {
    if (Math.hypot(at.x - hand.x, at.y - hand.y) <= r * 0.7) return "hand";
  }
  const dx = at.x - head.x;
  const dy = at.y - head.y;
  if (Math.hypot(dx, dy) <= r) return dy < -0.15 * r ? "cheek" : "head";
  const below = head.y - at.y;
  if (below >= 1.5 * r && below <= 2.8 * r && Math.abs(dx) >= 0.8 * r && Math.abs(dx) <= 2.8 * r) return "shoulder";
  return null;
}

/** 几秒里点了几下算「别闹了」。 */
export const PESTER_TAPS = 4;
export const PESTER_MS = 6000;

/** 最近几次点的时刻（毫秒）里，`now` 这一下是不是在闹她。 */
export function isPestering(taps: readonly number[], now: number): boolean {
  return taps.filter((t) => now - t <= PESTER_MS).length >= PESTER_TAPS;
}

/** 她的反应：身体动作、表情、要不要脸红，各取衣装里有的第一个。 */
export type Reaction = { body: string | null; face: string | null; blush: boolean };

const BODY: Record<TouchPart | "pester", readonly string[]> = {
  head: ["touch", "shy1", "shy2", "smile2", "smile1"],
  cheek: ["surp1", "surp2", "shy1", "touch"],
  hand: ["shy1", "smile1", "touch"],
  shoulder: ["surp1", "smile1"],
  pester: ["anger1", "anger2", "surp1"],
};

const FACE: Record<TouchPart | "pester", readonly string[]> = {
  head: ["face_shy", "face_shy2", "face_smile", "face_smile2"],
  cheek: ["face_surp", "face_shy", "face_wait"],
  hand: ["face_shy", "face_smile", "face_smile2"],
  shoulder: ["face_surp", "face_smile", "face_wait"],
  pester: ["face_anger", "face_anger1", "face_anger2"],
};

export function reactionTo(part: TouchPart, pestered: boolean, has: (name: string) => boolean): Reaction {
  const key = pestered ? "pester" : part;
  return {
    body: BODY[key].find(has) ?? null,
    face: FACE[key].find(has) ?? null,
    blush: !pestered && part !== "shoulder" && has("cheek_red"),
  };
}

/** 约会、偶遇里摸她，当面做的动作（发给她，她用台词回应）。 */
export const TOUCH_ACTIONS: Record<TouchPart, string> = {
  head: "摸摸她的头",
  cheek: "轻轻戳了戳她的脸颊",
  hand: "牵起她的手",
  shoulder: "轻轻拍了拍她的肩膀",
};

/** 一直戳她时发的那一句。 */
export const PESTER_ACTION = "一直戳她，闹个不停";

import { describe, expect, it } from "vitest";

import { conversationLog, emptyLog } from "@/lib/conversationLog";
import { actionText, stopPictureUrl } from "@/lib/outing";

describe("约会（GLOSSARY.md「约会」）", () => {
  it("小动作作为这一回合说的话，写成（动作：……），后面可以接着说", () => {
    expect(actionText(" 牵起她的手 ")).toBe("（动作：牵起她的手）");
    expect(actionText("递给她一杯饮料", "给，热的")).toBe("（动作：递给她一杯饮料）给，热的");
  });

  it("没说到地图上的地方时，背景按在街上随便逛找", () => {
    expect(stopPictureUrl(null, "meet", 3)).toBe("/api/outings/background/around/meet?v=3");
    expect(stopPictureUrl("aquarium", "jellyfish")).toBe("/api/outings/background/aquarium/jellyfish?v=0");
  });

  it("对话里的约会卡带着她的回顾", () => {
    const card = { outing_id: 1, spot: "aquarium", what: "去水族馆", day: "2026-10-25", recap: "水母好漂亮", started_at: "", photos: 0 };
    const log = conversationLog(emptyLog, {
      type: "loaded",
      messages: [{ type: "event", id: 9, kind: "outing", created_at: "2026-10-25T09:00:00Z", affinity: null, outing: card }],
    });
    const [entry] = log.entries;
    expect(entry.kind === "event" && entry.outing).toEqual(card);
  });
});

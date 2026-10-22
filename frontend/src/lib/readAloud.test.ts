import { describe, expect, it } from "vitest";

import type { Entry } from "@/lib/conversationLog";
import { readableKey, voiceUrl } from "@/lib/readAloud";

const hers = (key: string, pending = false, ja = "おはよう"): Entry => ({
  kind: "character",
  key,
  segments: ja ? [{ ja, zh: "早上好" }] : [],
  pending,
  at: "2026-10-22T08:00:00Z",
  affinity: null,
  photo: null,
});

describe("朗读", () => {
  it("她说完的回复和搭话能念", () => {
    expect(readableKey(hers("m12"))).toBe("m12");
    expect(readableKey(hers("r3"))).toBe("r3");
    expect(voiceUrl("m12")).toBe("/api/voice/m12");
  });

  it("还在说的、没字的、还没进记录的、制作人的不能念", () => {
    expect(readableKey(hers("m12", true))).toBeNull();
    expect(readableKey(hers("m12", false, ""))).toBeNull();
    expect(readableKey(hers("reply-1"))).toBeNull();
    expect(readableKey({ kind: "user", key: "m11", text: "早", at: "2026-10-22T08:00:00Z" })).toBeNull();
  });
});

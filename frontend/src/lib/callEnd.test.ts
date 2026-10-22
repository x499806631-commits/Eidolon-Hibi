import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { droppedEnd, backendEnd, endCard, startFailedEnd, MicUnavailable } from "@/lib/callEnd";

describe("startFailedEnd", () => {
  it.each([
    ["the microphone is refused", new MicUnavailable("no mic"), "mic_denied", null],
    ["the hour's calls are used up", new ApiError("本小时的通话次数已用完", 429), "over_limit", "本小时的通话次数已用完"],
    ["calls are unavailable", new ApiError("通话暂时不可用", 503), "not_connected", "通话暂时不可用"],
    ["the connection cannot be made", new Error("无法接通，请稍后再试"), "not_connected", null],
    ["she does not pick up, in the bath", new ApiError("她现在没有接电话", 409, { code: "no_answer" }), "no_answer", null],
  ])("says why when %s, and that it did not count", (_, error, reason, detail) => {
    // GLOSSARY.md「限额」：被拒绝的、没能接通的通话都不计入。
    expect(startFailedEnd(error)).toEqual({ reason, detail, counts: false });
  });
});

describe("backendEnd", () => {
  it("keeps the backend's word on whether the call counted", () => {
    expect(backendEnd({ type: "ended", reason: "duration_limit", message: "满 30 分钟了", counts: true })).toEqual({
      reason: "duration_limit",
      detail: null,
      counts: true,
    });
    expect(backendEnd({ type: "ended", reason: "not_connected", message: "语音服务出了故障", counts: false })).toEqual({
      reason: "not_connected",
      detail: null,
      counts: false,
    });
  });

  it("keeps when the cooldown she hung up for ends", () => {
    const end = backendEnd({
      type: "ended",
      reason: "cooldown",
      message: "她挂断了电话。",
      counts: true,
      cooldown_until: "2026-10-14T01:30:00+00:00",
    });

    expect(end).toEqual({ reason: "cooldown", detail: null, counts: true, cooldownUntil: "2026-10-14T01:30:00+00:00" });
  });

  it("keeps which own key failed", () => {
    const end = backendEnd({ type: "ended", reason: "own_key", message: "你的语音识别（Soniox）密钥调用失败", counts: false });

    expect(end.detail).toBe("你的语音识别（Soniox）密钥调用失败");
  });
});

describe("droppedEnd", () => {
  it("counts a connected call that drops, unless it is on the user's own keys alone", () => {
    expect(droppedEnd(false)).toEqual({ reason: "dropped", detail: null, counts: true });
    expect(droppedEnd(true).counts).toBe(false);
  });
});

describe("endCard", () => {
  const now = new Date("2026-10-13T03:00:00Z");
  const context = { name: "真乃", now, timeZone: "Asia/Tokyo", giftsOpen: true, exemptingKeys: ["llm", "tts", "stt"] } as const;

  it("says only that she hung up, when she did for what the user said, and offers a redial", () => {
    expect(endCard({ reason: "she_hung_up", detail: null, counts: true }, context)).toEqual({
      title: "真乃挂断了电话。",
      detail: null,
      body: null,
      counts: true,
      action: "redial",
    });
  });

  it("says she did not pick up, in the bath, and offers a redial (GLOSSARY.md「洗澡」)", () => {
    expect(endCard({ reason: "no_answer", detail: null, counts: false }, context)).toEqual({
      title: "真乃没有接电话",
      detail: null,
      body: "她现在好像不方便接电话，晚一点再打给她吧。",
      counts: false,
      action: "redial",
    });
  });

  it("says when she will talk again, when she hung up for a cooldown, and offers a gift instead of a redial", () => {
    const end = { reason: "cooldown", detail: null, counts: true, cooldownUntil: "2026-10-14T01:30:00Z" } as const;

    expect(endCard(end, context)).toEqual({
      title: "真乃挂断了电话。",
      detail: null,
      body: "她现在不想说话，明天 10:30 以后可以再找她。",
      counts: true,
      action: "gift",
    });
  });

  it("offers nothing but going back after a cooldown, while gifts are not open", () => {
    const end = { reason: "cooldown", detail: null, counts: true, cooldownUntil: "2026-10-14T01:30:00Z" } as const;

    expect(endCard(end, { ...context, giftsOpen: false })).toMatchObject({ action: null });
  });

  it("gives the other ends their own title and body, and the main button they call for", () => {
    expect(endCard({ reason: "turn_limit", detail: null, counts: true }, context)).toMatchObject({
      title: "这次通话说到回合数上限了",
      action: "redial",
    });
    expect(endCard({ reason: "own_key", detail: "密钥调用失败", counts: false }, context)).toMatchObject({
      detail: "密钥调用失败",
      counts: false,
      action: "own_keys",
    });
  });

  it("names the exempting keys when the user is over the limit for calls, and offers the own keys", () => {
    const end = { reason: "over_limit", detail: "这一小时的通话次数已经用完了", counts: false } as const;

    expect(endCard(end, { ...context, exemptingKeys: ["llm"] })).toEqual({
      title: "通话次数超出了限额",
      detail: "这一小时的通话次数已经用完了",
      body: "这个部署上，带上自己的 LLM 的通话不计入限额。",
      counts: false,
      action: "own_keys",
    });
  });
});

import { describe, expect, it } from "vitest";

import type { Draft } from "@/lib/producerProfile";
import {
  settingsCategory,
  settingsGroups,
  settingsHref,
  settingsRedirect,
  settingsSummaries,
  shownCategory,
  type SettingsValues,
} from "@/lib/settingsCategory";

describe("路径和类别互转", () => {
  it("一类的路径读回来还是这一类", () => {
    expect(settingsHref("own-keys")).toBe("/settings/own-keys");
    expect(settingsCategory(settingsHref("own-keys"))).toBe("own-keys");
  });

  it.each(["/settings", "/settings/xxx"])("%s 不是哪一类的二级页面", (path) => {
    expect(settingsCategory(path)).toBeNull();
  });

  it("只打开 /settings 时右栏显示档案，二级页面显示它自己那一类", () => {
    expect(shownCategory("/settings")).toBe("profile");
    expect(shownCategory("/settings/call")).toBe("call");
  });
});

describe("一级列表有哪几类", () => {
  it("搭话放在通话和外观之间", () => {
    expect(settingsGroups(false)[1]).toEqual(["call", "reach-outs", "appearance", "own-keys"]);
  });

  it("账号里没有搭话（部署者没开）时没有这一类，别的照旧", () => {
    expect(settingsGroups(null)).toEqual([
      ["profile", "memories", "experiences", "stats"],
      ["call", "appearance", "own-keys"],
      ["sign-in", "data-package"],
      ["about"],
    ]);
  });
});

describe("旧地址和不认识的地址换成哪个路径", () => {
  it.each([
    ["/settings?tab=own-keys", "/settings/own-keys"],
    ["/memories", "/settings/memories"],
  ])("旧地址 %s 换成 %s", (from, to) => {
    expect(settingsRedirect(from)).toBe(to);
  });

  it("换的时候留着地址里别的参数", () => {
    expect(settingsRedirect("/settings?tab=sign-in&linked=google")).toBe("/settings/sign-in?linked=google");
  });

  it.each(["/settings?tab=xxx", "/settings/xxx"])("不认识的 %s 回到 /settings", (from) => {
    expect(settingsRedirect(from)).toBe("/settings");
  });

  it.each(["/settings", "/settings/own-keys"])("%s 不用换", (href) => {
    expect(settingsRedirect(href)).toBeNull();
  });
});

describe("一级列表上的摘要", () => {
  const blank: Draft = { formOfAddress: "", gender: "", timezone: "", birthMonth: null, birthDay: null };
  const loading: SettingsValues = {
    profile: null,
    detectedTimezone: "Asia/Shanghai",
    memoryCount: null,
    daysKnown: null,
    call: null,
    appearance: null,
    reachOuts: null,
    ownServices: null,
    signInMethods: null,
  };

  it("还没读到值时每一类都没有摘要（关于从来没有）", () => {
    expect(settingsSummaries(loading)).toEqual({});
  });

  it("档案是称呼 · 时区；没填称呼、时区自动时照实说", () => {
    const filled = { ...blank, formOfAddress: "小林さん", timezone: "Asia/Tokyo" };
    expect(settingsSummaries({ ...loading, profile: { saved: filled, draft: filled } }).profile).toBe(
      "小林さん · Asia/Tokyo",
    );
    expect(settingsSummaries({ ...loading, profile: { saved: blank, draft: blank } }).profile).toBe(
      "按各自的习惯 · 自动（Asia/Shanghai）",
    );
  });

  it("档案改了没保存时整句换成「有修改未保存」", () => {
    const draft = { ...blank, formOfAddress: "小林さん" };
    expect(settingsSummaries({ ...loading, profile: { saved: blank, draft } }).profile).toBe("有修改未保存");
  });

  it("记忆是条数，一条都没有时「还没有记忆」", () => {
    expect(settingsSummaries({ ...loading, memoryCount: 3 }).memories).toBe("3 条");
    expect(settingsSummaries({ ...loading, memoryCount: 0 }).memories).toBe("还没有记忆");
  });

  it("统计是认识了几天", () => {
    expect(settingsSummaries({ ...loading, daysKnown: 12 }).stats).toBe("已认识 12 天");
  });

  it("通话是通话音质 · 电话质感开／关", () => {
    const call = { micId: "", quality: "high", muted: false, phoneTexture: true } as const;
    expect(settingsSummaries({ ...loading, call }).call).toBe("高保真 · 电话质感开");
    expect(settingsSummaries({ ...loading, call: { ...call, phoneTexture: false } }).call).toBe("高保真 · 电话质感关");
  });

  it("外观是 SCSP 头像开／关", () => {
    expect(settingsSummaries({ ...loading, appearance: { scspAvatars: true } }).appearance).toBe("SCSP 头像开");
    expect(settingsSummaries({ ...loading, appearance: { scspAvatars: false } }).appearance).toBe("SCSP 头像关");
  });

  it("搭话是开／关", () => {
    expect(settingsSummaries({ ...loading, reachOuts: true })["reach-outs"]).toBe("开");
    expect(settingsSummaries({ ...loading, reachOuts: false })["reach-outs"]).toBe("关");
  });

  it("自带密钥按 LLM、语音合成、语音识别的顺序列出填了的，一项都没填时「全部用部署者的服务」", () => {
    expect(settingsSummaries({ ...loading, ownServices: ["stt", "llm"] })["own-keys"]).toBe("已填 LLM、语音识别");
    expect(settingsSummaries({ ...loading, ownServices: [] })["own-keys"]).toBe("全部用部署者的服务");
  });

  it("登录与数据是已绑定的登录方式，用「、」连", () => {
    expect(settingsSummaries({ ...loading, signInMethods: ["github", "google"] })["sign-in"]).toBe("GitHub、Google");
  });
});

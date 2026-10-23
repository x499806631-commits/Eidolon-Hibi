import { describe, expect, it } from "vitest";

import {
  currentNavItem,
  departure,
  isGiftsInView,
  isInShell,
  isTyping,
  navDestination,
  showsShellCallBar,
} from "@/lib/navShell";

describe("在不在导航外壳里", () => {
  it.each([
    "/conversations",
    "/conversation/mano",
    "/conversation/mano?x=1",
    "/settings",
    "/settings?tab=own-keys",
    "/settings/own-keys",
    "/gifts?to=mano",
  ])("%s 在外壳里", (href) => {
    expect(isInShell(href)).toBe(true);
  });

  it.each([
    "/",
    "/about",
    "/privacy",
    "/terms",
    "/admin",
    "/admin/users/1",
    "/login",
    "/consent",
    "/conversation",
    "/conversations-old",
    "/settingsx",
    "/gifts/cake",
    "/api/auth/link/google",
  ])("%s 在外壳外面", (href) => {
    expect(isInShell(href)).toBe(false);
  });

  it("别的网站上的网址不算，哪怕路径一样", () => {
    expect(isInShell("https://github.com/conversations")).toBe(false);
  });
});

describe("外壳顶上的通话条", () => {
  it("没在通话时哪里都没有", () => {
    expect(showsShellCallBar("/settings", null)).toBe(false);
    expect(showsShellCallBar("/conversations", null)).toBe(false);
  });

  it("通话中在设置、礼物、对话列表、别人的对话页上出现", () => {
    expect(showsShellCallBar("/settings", "mano")).toBe(true);
    expect(showsShellCallBar("/gifts", "mano")).toBe(true);
    expect(showsShellCallBar("/settings?tab=call", "mano")).toBe(true);
    expect(showsShellCallBar("/conversations", "mano")).toBe(true);
    expect(showsShellCallBar("/conversation/hiori", "mano")).toBe(true);
  });

  it("她自己的对话页上不出现：那里有页首下面那条", () => {
    expect(showsShellCallBar("/conversation/mano", "mano")).toBe(false);
  });

  it("外壳外面不出现", () => {
    expect(showsShellCallBar("/admin", "mano")).toBe(false);
  });
});

describe("去某个网址时通话怎么样", () => {
  it("没在通话时哪里都直接走", () => {
    for (const to of ["/conversations", "/conversation/hiori", "/settings", "/admin", "/about"])
      expect(departure(to, null), to).toBe("go");
  });

  it("通话中在外壳里换页，通话留着", () => {
    expect(departure("/conversations", "mano")).toBe("keep");
    expect(departure("/settings", "mano")).toBe("keep");
    expect(departure("/settings?tab=own-keys", "mano")).toBe("keep");
    expect(departure("/settings/memories", "mano")).toBe("keep");
    expect(departure("/gifts?to=hiori", "mano")).toBe("keep");
  });

  it("通话中回她自己的对话页，通话留着", () => {
    expect(departure("/conversation/mano", "mano")).toBe("keep");
  });

  it("通话中打开另一位角色的对话，先问再挂断", () => {
    expect(departure("/conversation/hiori", "mano")).toBe("ask");
  });

  it("通话中离开外壳，先问再挂断", () => {
    for (const to of ["/admin", "/about", "/privacy", "/terms", "/", "/api/auth/link/google"])
      expect(departure(to, "mano"), to).toBe("ask");
  });
});

describe("导航栏和 dock 的当前项", () => {
  it.each([
    ["/conversation/mano", "conversations"],
    ["/settings", "settings"],
    ["/settings/own-keys", "settings"],
    ["/gifts?to=mano", "gifts"],
    ["/calendar", "calendar"],
    ["/letters", "letters"],
    ["/diary", "diary"],
  ] as const)("%s 的当前项是 %s", (href, item) => {
    expect(currentNavItem(href)).toBe(item);
  });

  it("外壳外面没有当前项", () => {
    expect(currentNavItem("/admin")).toBeNull();
  });
});

describe("礼物页在不在眼前（到账当场记成看过，「礼物」不出点）", () => {
  it("在礼物页上就在眼前，带着预选的也是", () => {
    expect(isGiftsInView("/gifts", false)).toBe(true);
    expect(isGiftsInView("/gifts?to=mano", false)).toBe(true);
  });

  it("通话层展开着盖住礼物页时不在眼前", () => {
    expect(isGiftsInView("/gifts", true)).toBe(false);
  });

  it("别的页面上不在眼前", () => {
    expect(isGiftsInView("/conversation/mano", false)).toBe(false);
  });
});

describe("点导航栏、dock 里的一项去哪", () => {
  it("点别的项，去那一项的第一级", () => {
    expect(navDestination("settings", "/conversation/mano")).toBe("/settings");
    expect(navDestination("gifts", "/settings/call")).toBe("/gifts");
  });

  it("在二级页面上（她的对话页、设置的一类）再点当前那一项，回到这一项的第一级", () => {
    expect(navDestination("conversations", "/conversation/mano")).toBe("/conversations");
    expect(navDestination("settings", "/settings/call")).toBe("/settings");
  });

  it("已经在第一级时再点当前那一项，不动", () => {
    expect(navDestination("conversations", "/conversations")).toBeNull();
    expect(navDestination("gifts", "/gifts")).toBeNull();
  });
});

describe("手机上在打字（dock 藏起来）", () => {
  it.each([
    ["textarea", { tagName: "TEXTAREA" }],
    ["搜索框", { tagName: "INPUT", type: "search" }],
    ["可编辑元素", { tagName: "DIV", isContentEditable: true }],
  ])("焦点在%s上算", (_, focused) => {
    expect(isTyping(focused)).toBe(true);
  });

  it.each([
    ["没有焦点", null],
    ["勾选框", { tagName: "INPUT", type: "checkbox" }],
    ["按钮", { tagName: "BUTTON" }],
  ])("%s不算", (_, focused) => {
    expect(isTyping(focused)).toBe(false);
  });
});

"use client";

import NextLink from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore, type MouseEvent } from "react";

import { opensNewTab, useDirectory } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import type { Account } from "@/lib/api";
import { hasUnseen } from "@/lib/conversationList";
import { unreadDiary } from "@/lib/diary";
import { unseenKeepsakes } from "@/lib/keepsakes";
import { unreadLetters } from "@/lib/letters";
import { currentNavItem, FIRST_LEVEL, isTyping, NAV_ITEMS, navDestination, type NavItem } from "@/lib/navShell";
import { REPOSITORY_URL } from "@/lib/repository";
import { cn } from "@/lib/utils";
import {
  CalendarIcon,
  ConversationsIcon,
  DiaryIcon,
  GiftIcon,
  KeepsakesIcon,
  GitHubIcon,
  LettersIcon,
  SettingsIcon,
} from "@/theme/icons";

const ICONS: Record<NavItem, typeof SettingsIcon> = {
  conversations: ConversationsIcon,
  gifts: GiftIcon,
  calendar: CalendarIcon,
  letters: LettersIcon,
  diary: DiaryIcon,
  keepsakes: KeepsakesIcon,
  settings: SettingsIcon,
};

/**
 * 导航栏（电脑）：最左一条 72px，右边一条细线，不能收起；对话、礼物、日历、信箱、日记、设置从上往下，GitHub 仓库链接在最底下
 * （DESIGN.md「Layout」）。窄屏由 CSS 藏起来，换成 dock。通话层只盖住它右边，展开时它照样能点（见 NavLinks）。
 */
export function NavRail() {
  return (
    <nav
      aria-label={copy.nav.label}
      className="flex w-18 shrink-0 flex-col items-center gap-2 border-r bg-background py-3 max-md:hidden"
    >
      <NavLinks className="w-16 gap-1 rounded-lg py-1" />
      <div className="flex-1" />
      {/* 新标签页打开：通话照常（外壳的去处规则）。 */}
      <Button
        variant="ghost"
        size="icon-lg"
        aria-label={copy.app.repositoryLabel}
        nativeButton={false}
        render={<a href={REPOSITORY_URL} target="_blank" rel="noopener noreferrer" />}
      >
        <GitHubIcon />
      </Button>
    </nav>
  );
}

/**
 * dock（手机）：底部一条，项目同导航栏，实底、上沿一条细线、留出 safe-area；宽屏由 CSS 藏起来。
 * 输入框拿到焦点（键盘弹起）时藏起来，失去焦点后回来。
 */
export function NavDock({ isInert }: { isInert: boolean }) {
  const typing = useTyping();
  return (
    <nav
      aria-label={copy.nav.label}
      inert={isInert}
      className={cn(
        "grid shrink-0 auto-cols-fr grid-flow-col border-t bg-background pb-[env(safe-area-inset-bottom)] md:hidden",
        typing && "hidden",
      )}
    >
      <NavLinks className="h-14 gap-0.5" />
    </nav>
  );
}

/**
 * 导航的每一项：图标在上、字在下；当前项（按网址，外壳的去处规则）图标铺中性的 bg-muted 圆角底、字 foreground 加 medium，
 * 标 `aria-current="page"`。点别的项去那一项的第一级；在二级页面上（她的对话页）再点当前项回到第一级，已在第一级时不动。
 * 通话层展开着（只有电脑上点得到，手机上它盖住 dock）时先收起，结束卡就放下，通话照常，去处顶上是通话条。
 * 对话列表里有一行有没看过的搭话（正开着的那段对话除外）时，「对话」图标右上角一个前景色的小点，和公告铃铛上的一样；
 * 有没读过的信时，「信箱」图标上也是这样一个点；有能读又没读过的日记时，「日记」图标上也是。
 */
function NavLinks({ className }: { className: string }) {
  const pathname = usePathname();
  const { guard, held, characters, latest, preferences, onScreen, account } = useDirectory();
  const current = currentNavItem(pathname);
  const unseen = characters !== null && hasUnseen(characters, latest, { preferences, onScreen });
  const unread = useSyncExternalStore(unreadLetters.subscribe, unreadLetters.snapshot, () => 0) > 0;
  const unreadEntries = useSyncExternalStore(unreadDiary.subscribe, unreadDiary.snapshot, () => 0) > 0;
  const unseenGifts = useSyncExternalStore(unseenKeepsakes.subscribe, unseenKeepsakes.snapshot, () => 0) > 0;
  const dotted: Partial<Record<NavItem, string>> = {
    ...(unseen ? { conversations: copy.nav.conversationsUnseen } : {}),
    ...(unread ? { letters: copy.nav.lettersUnread } : {}),
    ...(unreadEntries ? { diary: copy.nav.diaryUnread } : {}),
    ...(unseenGifts ? { keepsakes: copy.nav.keepsakesUnseen } : {}),
  };
  // 关掉了日记、来信的（GLOSSARY.md「玩法开关」）不显示那一项。
  const shown = NAV_ITEMS.filter((item) => !hiddenBySwitch(item, account?.features));
  return shown.map((item) => {
    const Icon = ICONS[item];
    const isCurrent = item === current;
    const onClick = (event: MouseEvent<HTMLAnchorElement>) => {
      const to = navDestination(item, pathname);
      if (to === null) event.preventDefault();
      else guard(to)(event);
      if (!opensNewTab(event) && held?.snapshot().layer?.collapsed === false) {
        held.collapse();
        held.dismissEnd();
      }
    };
    return (
      <NextLink
        key={item}
        href={FIRST_LEVEL[item]}
        onClick={onClick}
        aria-current={isCurrent ? "page" : undefined}
        aria-label={dotted[item]}
        className={cn(
          "group flex flex-col items-center justify-center text-meta outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
          className,
        )}
      >
        <span
          className={cn(
            "relative flex h-8 w-12 items-center justify-center rounded-full transition-colors",
            isCurrent ? "bg-muted text-foreground" : "text-muted-foreground group-hover:bg-muted/60",
          )}
        >
          <Icon aria-hidden className="size-5" />
          {dotted[item] && (
            <span aria-hidden className="absolute top-1 right-2.5 size-1.5 rounded-full bg-foreground" />
          )}
        </span>
        <span className={isCurrent ? "font-medium text-foreground" : "text-muted-foreground"}>{copy.nav[item]}</span>
      </NextLink>
    );
  });
}

/** 用户自己关掉了的玩法，导航里不显示它那一项（GLOSSARY.md「玩法开关」）。 */
export function hiddenBySwitch(item: NavItem, features: Account["features"] | undefined): boolean {
  return (item === "diary" || item === "letters" || item === "keepsakes") && features?.[item] === false;
}

/** 手机上在打字（焦点在输入框里），大致就是键盘弹起来了。失去焦点那一刻焦点还没落下，等它落下再看。 */
function useTyping(): boolean {
  const [typing, setTyping] = useState(false);
  useEffect(() => {
    let settling: ReturnType<typeof setTimeout> | undefined;
    const check = () => setTyping(isTyping(document.activeElement as HTMLInputElement | null));
    const checkOnceSettled = () => {
      clearTimeout(settling);
      settling = setTimeout(check, 0);
    };
    document.addEventListener("focusin", check);
    document.addEventListener("focusout", checkOnceSettled);
    return () => {
      clearTimeout(settling);
      document.removeEventListener("focusin", check);
      document.removeEventListener("focusout", checkOnceSettled);
    };
  }, []);
  return typing;
}

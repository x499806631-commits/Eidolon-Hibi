"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { copy } from "@/i18n";
import { AchievementLine, UnseenDot } from "@/components/app/AchievementLine";
import { COMPLETE, completeRow, type HerAchievements } from "@/lib/achievements";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { SuccessIcon, TaskOpenIcon } from "@/theme/icons";

const t = copy.achievements;

/**
 * 「成就」面板（DESIGN.md「Overlays」）：她的对话页更多菜单的「成就」打开，宽屏居中的 Dialog，窄屏底部的 Drawer，
 * 都用她的颜色（同「历史对话」面板）。她的四个成就一行一个（`AchievementLine`），最后一行是「集齐」（`completeRow`）。
 * 新解锁的那几行（`unseen`，成就 id，集齐是 `COMPLETE`）名字旁一个点。
 */
export function AchievementsPanel({
  isOpen,
  onOpenChange,
  isWide,
  color,
  her,
  unseen,
}: {
  isOpen: boolean;
  onOpenChange: (open: boolean) => void;
  isWide: boolean;
  /** 她的官方色。 */
  color: string;
  her: HerAchievements | null;
  unseen: ReadonlySet<string>;
}) {
  const body = her && (
    <ul className="flex flex-col">
      {her.achievements.map((achievement) => (
        <AchievementLine key={achievement.id} achievement={achievement} isUnseen={unseen.has(achievement.id)} />
      ))}
      <CompleteLine her={her} isUnseen={unseen.has(COMPLETE)} />
    </ul>
  );

  // 浮层渲染到对话子树外面，在内容上再注入一次她的颜色（DESIGN.md「The Scoped Colour Rule」）。
  const style = herVars(color);
  if (isWide) {
    return (
      <Dialog open={isOpen} onOpenChange={onOpenChange}>
        <DialogContent style={style} className="flex max-h-[80dvh] flex-col sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
          </DialogHeader>
          <div className="min-h-0 overflow-y-auto">{body}</div>
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={isOpen} onOpenChange={onOpenChange} showSwipeHandle>
      <DrawerContent style={style}>
        <DrawerHeader>
          <DrawerTitle>{t.title}</DrawerTitle>
        </DrawerHeader>
        <div className="min-h-0 overflow-y-auto px-4 pb-6">{body}</div>
      </DrawerContent>
    </Drawer>
  );
}

/** 「集齐」那一行：没集齐是空圈、「集齐」和「重置卡 ×1」，集齐了是勾和「集齐了」。 */
function CompleteLine({ her, isUnseen }: { her: HerAchievements; isUnseen: boolean }) {
  const row = completeRow(her.complete);
  const Icon = row.isComplete ? SuccessIcon : TaskOpenIcon;
  return (
    <li className="flex min-h-14 items-center gap-3 border-t">
      <Icon aria-hidden className={cn("size-5 shrink-0", !row.isComplete && "text-muted-foreground")} />
      <span className={cn("flex min-w-0 flex-1 items-center gap-2 text-body", !row.isComplete && "text-muted-foreground")}>
        {row.name}
        {isUnseen && <UnseenDot />}
      </span>
      {row.reward && <span className="shrink-0 text-meta text-muted-foreground">{row.reward}</span>}
    </li>
  );
}

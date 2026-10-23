import { copy } from "@/i18n";
import { achievementRow, type Achievement } from "@/lib/achievements";
import { cn } from "@/lib/utils";

const t = copy.achievements;

/**
 * 一个成就那一行（`achievementRow`）：名字（新解锁时旁边一个点），下面一行只写下一个阶段，右边阶段点和下一阶段的奖励。
 * 她的对话页的成就面板和礼物页那一小节共用（DESIGN.md「成就面板」「礼物页」）。
 */
export function AchievementLine({ achievement, isUnseen }: { achievement: Achievement; isUnseen: boolean }) {
  const row = achievementRow(achievement);
  return (
    <li className="flex min-h-14 items-center gap-3 py-1">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="flex items-center gap-2 text-body">
          {row.name}
          {isUnseen && <UnseenDot />}
        </span>
        <span className="text-meta text-muted-foreground tabular-nums">{row.progress}</span>
      </div>
      <Stages stages={row.stages} />
      <span className="w-14 shrink-0 text-right text-meta text-muted-foreground">{row.reward}</span>
    </li>
  );
}

/** 阶段点：解锁的实心，没解锁的空心（淡）。 */
function Stages({ stages }: { stages: boolean[] }) {
  const unlocked = stages.filter(Boolean).length;
  return (
    <span role="img" aria-label={t.stagesLabel(unlocked, stages.length)} className="flex shrink-0 gap-1">
      {stages.map((isUnlocked, i) => (
        <span key={i} className={cn("size-2 rounded-full", isUnlocked ? "bg-foreground" : "bg-foreground/15")} />
      ))}
    </span>
  );
}

/** 新解锁的点：6px，`bg-foreground`，同导航上的点；读屏读「新解锁」。面板、礼物页那一小节和更多菜单的「成就」后面都用它。 */
export function UnseenDot({ className }: { className?: string }) {
  return (
    <span className={cn("size-1.5 shrink-0 rounded-full bg-foreground", className)}>
      <span className="sr-only">{t.unseen}</span>
    </span>
  );
}

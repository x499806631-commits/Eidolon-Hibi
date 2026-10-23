import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import type { DailyTaskAction, DailyTasksView } from "@/lib/dailyTasks";
import { cn } from "@/lib/utils";
import { SuccessIcon, TaskOpenIcon } from "@/theme/icons";

const t = copy.gifts.dailyTasks;

/**
 * 礼物页抽奖台下面那一节「每日任务」（DESIGN.md「礼物页」）：描边圆角块，标题、做完几项（「1/3」）、右边换新的时刻；
 * 每项一行，没做完空圈、做完打勾且字变淡划掉，目标不止 1 的没做完时带「3/10」，右边「抽奖 ×1」；
 * 没做完的旁边一个 ghost 键一步到能做它的地方（`onAction`：「去对话」「挑一件」）。三项都做完时整节收成一行。
 * 在这一页上刚做完的（`justDone`，id）打勾时弹一下，减少动态时不弹。
 */
export function DailyTasksSection({
  view,
  justDone = [],
  onAction,
}: {
  view: DailyTasksView;
  justDone?: readonly string[];
  onAction: (action: DailyTaskAction) => void;
}) {
  if (view.isAllDone)
    return (
      <section aria-label={t.title} className="flex min-h-11 items-center gap-3 rounded-2xl border px-5 py-3">
        <SuccessIcon
          aria-hidden
          className={cn("size-5 shrink-0", justDone.length > 0 && "motion-safe:animate-jump")}
        />
        <p className="min-w-0 flex-1 text-body">{t.allDone}</p>
        <span className="shrink-0 text-meta text-muted-foreground">{view.renews}</span>
      </section>
    );
  return (
    <section aria-label={t.title} className="flex flex-col gap-1 rounded-2xl border px-5 py-3">
      <p className="flex items-baseline gap-2">
        <span className="text-header">{t.title}</span>
        <span className="text-sm text-muted-foreground tabular-nums">{view.count}</span>
        <span className="flex-1" />
        <span className="text-meta text-muted-foreground">{view.renews}</span>
      </p>
      <ul>
        {view.rows.map(({ action, ...row }) => (
          <li key={row.id} className="flex min-h-11 items-center gap-3">
            {row.isDone ? (
              <SuccessIcon
                aria-hidden
                className={cn("size-5 shrink-0", justDone.includes(row.id) && "motion-safe:animate-jump")}
              />
            ) : (
              <TaskOpenIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
            )}
            <span className={cn("min-w-0 flex-1 text-body", row.isDone && "text-muted-foreground line-through")}>
              {row.name}
              {row.progress && (
                <span className="ml-2 text-meta text-muted-foreground tabular-nums">{row.progress}</span>
              )}
            </span>
            {action && (
              // 手机上触控目标不小于 44px
              <Button
                variant="ghost"
                size="sm"
                className="shrink-0 max-md:h-11"
                onClick={() => onAction(action)}
              >
                {t.actions[action]}
              </Button>
            )}
            <span className="shrink-0 text-meta text-muted-foreground">{row.reward}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

"use client";

import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import type { HeldTicket, TicketKind } from "@/lib/tickets";
import { cn } from "@/lib/utils";
import {
  DateTicketIcon,
  DiaryKeyIcon,
  LetterTicketIcon,
  LikedGiftTicketIcon,
  PhotoTicketIcon,
} from "@/theme/icons";

const t = copy.gifts.tickets;

export const TICKET_ICONS: Record<TicketKind, typeof DateTicketIcon> = {
  liked_gift: LikedGiftTicketIcon,
  letter: LetterTicketIcon,
  diary_key: DiaryKeyIcon,
  photo: PhotoTicketIcon,
  date: DateTicketIcon,
};

/** 一种券的方块：和礼物插画同样的 `bg-muted` 圆角正方形，中间是这种券的图标。 */
export function TicketTile({ kind, size }: { kind: TicketKind; size: "small" | "large" | "fill" }) {
  const Icon = TICKET_ICONS[kind];
  return (
    <span
      aria-hidden
      className={cn(
        "relative flex aspect-square items-center justify-center rounded-2xl bg-muted",
        size === "small" ? "size-16 shrink-0" : size === "large" ? "w-40" : "w-full",
      )}
    >
      <Icon className={size === "small" ? "size-7" : "size-12"} strokeWidth={1.5} />
    </span>
  );
}

/**
 * 体验券（DESIGN.md「礼物页」）：有券时才出现，标题下每种一行（描边圆角块）：64px 的券方块、「券名 ×n」、
 * 一句它是做什么的，右边「使用」；日记钥匙在日记页上用，右边是「去日记页」。
 */
export function Tickets({
  tickets,
  onUse,
  onDiary,
}: {
  tickets: HeldTicket[];
  onUse: (kind: TicketKind) => void;
  onDiary: () => void;
}) {
  return (
    <section aria-label={t.title} className="flex flex-col gap-2">
      <h2 className="text-header">{t.title}</h2>
      <ul className="flex flex-col gap-2">
        {tickets.map(({ kind, count }) => (
          <li key={kind} className="flex flex-wrap items-center gap-4 rounded-2xl border px-5 py-4">
            <TicketTile kind={kind} size="small" />
            <div className="flex min-w-0 flex-1 flex-col gap-1">
              <p className="flex items-baseline gap-2">
                <span className="text-body font-medium">{t.kinds[kind].name}</span>
                <span aria-hidden className="text-sm text-muted-foreground tabular-nums">
                  {t.count(count)}
                </span>
                <span className="sr-only">{t.label(count)}</span>
              </p>
              <p className="text-subtitle text-muted-foreground">{t.kinds[kind].note}</p>
            </div>
            {kind === "diary_key" ? (
              <Button variant="outline" onClick={onDiary}>
                {t.toDiary}
              </Button>
            ) : (
              <Button variant="outline" onClick={() => onUse(kind)}>
                {t.use}
              </Button>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

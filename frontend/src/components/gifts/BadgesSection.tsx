"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/i18n";
import { getBadges, seeBadges, type Badge, type Badges } from "@/lib/badges";
import { cn } from "@/lib/utils";
import { BadgeIcon } from "@/theme/icons";

const t = copy.badges;

/**
 * 礼物页成就下面那一小节「称号」（GLOSSARY.md「称号」）：得到了几个、现在每天能偶遇和抽奖几次，
 * 点「看看称号」打开全部称号，得到的亮着、没得到的灰着；点一个看是怎么得到的。新得到的有个点。
 */
export function BadgesSection() {
  const [badges, setBadges] = useState<Badges | null>(null);
  const [isOpen, setOpen] = useState(false);
  const [picked, setPicked] = useState<Badge | null>(null);

  useEffect(() => {
    getBadges().then(setBadges, () => {}); // 读不到就不显示
  }, []);

  if (!badges) return null;
  const unseen = badges.badges.filter((b) => b.unseen).length;
  const open = () => {
    setOpen(true);
    if (unseen > 0) void seeBadges();
  };
  const close = () => {
    setOpen(false);
    setPicked(null);
    setBadges((b) => b && { ...b, badges: b.badges.map((x) => ({ ...x, unseen: false })) });
  };

  return (
    <section aria-label={t.title} className="flex flex-col gap-2 rounded-2xl border px-5 py-3">
      <div className="flex items-center gap-2">
        <p className="text-header">{t.title}</p>
        {unseen > 0 && <span aria-label={t.unseen(unseen)} className="size-2 rounded-full bg-primary" />}
        <p className="ml-auto text-meta text-muted-foreground">{t.count(badges.earned, badges.badges.length)}</p>
      </div>
      <p className="text-meta text-muted-foreground">
        {t.perk(badges.perk)}
        {badges.next !== null && ` ${t.next(badges.next - badges.earned, badges.perk + 1)}`}
      </p>
      <Button variant="outline" size="sm" className="self-start" onClick={open}>
        <BadgeIcon data-icon="inline-start" />
        {t.open}
      </Button>
      <Dialog open={isOpen} onOpenChange={(o) => (o ? open() : close())}>
        <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
            <DialogDescription>{t.hint}</DialogDescription>
          </DialogHeader>
          {picked && (
            <div aria-live="polite" className="rounded-xl bg-muted p-3">
              <p className="text-body font-medium">「{picked.name}」</p>
              <p className="text-meta text-muted-foreground">
                {picked.how}
                {picked.earned_at ? ` · ${t.earnedOn(picked.earned_at.slice(0, 10))}` : ` · ${t.notYet}`}
              </p>
            </div>
          )}
          {(["doing", "affinity"] as const).map((kind) => (
            <section key={kind} aria-label={t.kinds[kind]} className="flex flex-col gap-2">
              <h3 className="text-meta font-medium">{t.kinds[kind]}</h3>
              <ul className="flex flex-wrap gap-1.5">
                {badges.badges
                  .filter((b) => b.kind === kind)
                  .map((b) => (
                    <li key={b.id}>
                      <button
                        type="button"
                        aria-pressed={picked?.id === b.id}
                        onClick={() => setPicked(b)}
                        className={cn(
                          "relative rounded-full border px-3 py-1 text-sm transition-colors",
                          b.earned_at ? "border-primary/40 bg-primary/10" : "text-muted-foreground opacity-60",
                          picked?.id === b.id && "ring-2 ring-ring",
                        )}
                      >
                        {b.name}
                        {b.unseen && <span aria-hidden className="absolute -top-0.5 -right-0.5 size-2 rounded-full bg-primary" />}
                        <span className="sr-only">{b.earned_at ? t.earned : t.notYet}</span>
                      </button>
                    </li>
                  ))}
              </ul>
            </section>
          ))}
        </DialogContent>
      </Dialog>
    </section>
  );
}

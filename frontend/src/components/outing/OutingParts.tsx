"use client";

import { Button } from "@/components/ui/button";
import { copy } from "@/i18n";
import type { Character, OutingCard } from "@/lib/api";
import { DATE_SPOTS, type DateSpot } from "@/lib/cityMap";
import { OutingIcon } from "@/theme/icons";

const t = copy.outing;

/** 对话页页首下面那条（GLOSSARY.md「约会」）：今天和她约好了做什么，「去赴约」；见了面以后是「回到约会」。 */
export function OutingBanner({
  name,
  what,
  isOn,
  isEncounter = false,
  onOpen,
}: {
  name: string;
  what: string;
  isOn: boolean;
  /** 偶遇中（GLOSSARY.md「偶遇」）。 */
  isEncounter?: boolean;
  onOpen: () => void;
}) {
  return (
    <div className="flex shrink-0 items-center gap-2 border-b bg-her/10 px-4 py-2">
      <OutingIcon aria-hidden className="size-4 shrink-0 text-her" />
      <p className="min-w-0 flex-1 truncate text-sm">{isEncounter ? t.encounterBanner(name) : isOn ? t.bannerOn(name) : t.banner(name, what)}</p>
      <Button size="sm" variant="her" onClick={onOpen}>
        {isOn ? t.resume : t.go}
      </Button>
    </div>
  );
}

/** 地点的名字：地图上的那一处，没有就是约好做的事。 */
function placeOf(card: OutingCard): string {
  const places = copy.map.places as Record<string, string>;
  const spot = DATE_SPOTS.find((s) => s === card.spot) as DateSpot | undefined;
  if (spot) return copy.map.places[spot];
  return (card.spot && places[card.spot]) || card.what;
}

/** 对话里的约会卡：哪天、去了哪，和她自己的回顾。 */
export function OutingCardView({ card, her }: { card: OutingCard; her: Character }) {
  const [, month, day] = card.day.split("-").map(Number);
  return (
    <article className="w-full max-w-sm rounded-2xl border bg-background p-4 shadow-sm">
      <header className="mb-2 flex items-center gap-2 text-sm font-medium">
        <OutingIcon aria-hidden className="size-4 text-her" />
        {card.kind === "encounter" ? t.encounterCard(month, day, placeOf(card)) : t.card(month, day, placeOf(card))}
      </header>
      {card.recap && <p className="text-body leading-relaxed text-muted-foreground">{card.recap}</p>}
      <p className="sr-only">{card.kind === "encounter" ? t.encounterOver(her.name) : t.over(her.name)}</p>
    </article>
  );
}

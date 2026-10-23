"use client";

import { useState } from "react";

import { SectionHeading } from "@/components/app/SubpageHeader";
import { GiftTile } from "@/components/gifts/GiftTile";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import { GIFT_FILTERS, shownGifts, type GiftFilter, type GiftInfo, type Held, type Owned } from "@/lib/gifts";
import { DrinkIcon, FlowerIcon, FoodIcon, TrinketIcon } from "@/theme/icons";

const t = copy.gifts;

/** 「我的礼物」标题的 id：每日任务里的「挑一件」滚到这里。 */
export const MY_GIFTS_ID = "my-gifts";

const FILTER_ICONS = { food: FoodIcon, drink: DrinkIcon, flower: FlowerIcon, trinket: TrinketIcon };

/**
 * 我的礼物（DESIGN.md「礼物页」）：标题右边是类别筛选（`ToggleGroup`），下面是插画网格（手机 3 列，更宽 4、5 列）。
 * 点一件拉出送礼面板（`onGive`）。网格里不透露哪位角色喜欢什么。背包是空的时候由页面放「还没有礼物」，这里不出。
 */
export function MyGifts({
  backpack,
  catalog,
  onGive,
}: {
  backpack: Owned[];
  catalog: GiftInfo[];
  onGive: (held: Held) => void;
}) {
  const [filter, setFilter] = useState<GiftFilter>("all");
  const held = shownGifts(backpack, catalog, filter);
  return (
    <section aria-labelledby={MY_GIFTS_ID} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionHeading id={MY_GIFTS_ID}>{t.mine}</SectionHeading>
        <ToggleGroup
          aria-label={t.filters.label}
          size="sm"
          spacing={0}
          value={[filter]}
          onValueChange={(value: unknown[]) => {
            const next = value[0] as GiftFilter | undefined;
            if (next) setFilter(next);
          }}
        >
          {GIFT_FILTERS.map((f) => {
            const Icon = f === "all" ? null : FILTER_ICONS[f];
            return (
              <ToggleGroupItem key={f} value={f}>
                {Icon && <Icon data-icon="inline-start" />}
                {t.filters[f]}
              </ToggleGroupItem>
            );
          })}
        </ToggleGroup>
      </div>
      {held.length === 0 ? (
        <p className="py-8 text-center text-body text-muted-foreground">{t.noneOfKind}</p>
      ) : (
        <ul className="grid grid-cols-3 gap-3 sm:grid-cols-4 md:grid-cols-5">
          {held.map((h) => (
            <HeldGift key={h.gift.id} held={h} onGive={onGive} />
          ))}
        </ul>
      )}
    </section>
  );
}

/** 网格里的一格，点了送出：插画方块（多于 1 件时右下角「×n」）、礼物名、稀有度。读屏读名字、件数和稀有度。 */
function HeldGift({ held, onGive }: { held: Held; onGive: (held: Held) => void }) {
  const { gift, count } = held;
  const rarity = t.rarity[gift.rarity];
  return (
    <li>
      <button
        type="button"
        onClick={() => onGive(held)}
        className="group flex w-full flex-col gap-1 rounded-2xl text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="sr-only">{t.held(gift.name, count, rarity)}</span>
        <GiftTile gift={gift} size="fill" isHoverable>
          {count > 1 && (
            <span className="absolute right-2 bottom-1.5 text-meta font-medium tabular-nums">{t.count(count)}</span>
          )}
        </GiftTile>
        <span aria-hidden className="truncate text-sm">
          {gift.name}
        </span>
        <span aria-hidden className="text-meta text-muted-foreground">
          {rarity}
        </span>
      </button>
    </li>
  );
}

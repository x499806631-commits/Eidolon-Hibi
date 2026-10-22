"use client";

import { GiftTile } from "@/components/gifts/GiftTile";
import { TicketTile } from "@/components/gifts/Tickets";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { copy } from "@/i18n";
import type { Drawn, DrawResult, GiftInfo } from "@/lib/gifts";
import { cn } from "@/lib/utils";
import { GiftIcon, RareIcon } from "@/theme/icons";

const t = copy.gifts;

/** 抽奖弹窗现在的样子：礼物盒在跳（`result` 为 null），或者揭晓了抽到的。 */
export type Drawing = { all: boolean; result: DrawResult | null };

/** 一次揭晓里依次淡入的间隔。 */
const STAGGER_MS = 120;

/**
 * 抽奖弹窗（DESIGN.md「礼物页」）：居中的 `Dialog`，先是礼物盒图标跳约 0.9 秒（减少动态效果时不跳），
 * 然后抽到的东西放大淡入。单抽是一件大的，下面「送给谁…」（抽到礼物时，点了打开这件的送礼面板）、
 * 「再抽一次（还有 n 次）」（没次数时不出）和「收下」；全部抽完是 3 列小网格，一件件依次淡入。
 */
export function DrawDialog({
  drawing,
  catalog,
  onGive,
  onAgain,
  onClose,
}: {
  drawing: Drawing | null;
  catalog: Map<string, GiftInfo>;
  onGive: (gift: GiftInfo) => void;
  onAgain: () => void;
  onClose: () => void;
}) {
  const result = drawing?.result ?? null;
  const first = result?.drawn[0];
  const drawnGift = !drawing?.all && first?.kind === "gift" ? catalog.get(first.gift_id) : undefined;
  return (
    // 礼物盒还在跳时关不掉：接口回来以前收起，回来后又会弹出来，抽奖台的按钮也会提前能点。
    <Dialog open={drawing !== null} onOpenChange={(open) => !open && result !== null && onClose()}>
      <DialogContent showCloseButton={false}>
        <DialogTitle className="sr-only">
          {result === null ? t.drawing.opening : drawing?.all ? t.drawing.all(result.drawn.length) : t.drawing.one}
        </DialogTitle>
        {result === null ? (
          <div className="flex h-64 items-center justify-center">
            <GiftIcon aria-hidden className="size-16 text-muted-foreground motion-safe:animate-hop" />
          </div>
        ) : drawing?.all ? (
          <ul className="grid grid-cols-3 gap-3 py-2">
            {result.drawn.map((d, i) => (
              <li
                key={i}
                style={{ animationDelay: `${i * STAGGER_MS}ms` }}
                className="motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-90 motion-safe:fill-mode-both"
              >
                <DrawnItem drawn={d} catalog={catalog} />
              </li>
            ))}
          </ul>
        ) : (
          <div className="py-2 motion-safe:animate-in motion-safe:fade-in motion-safe:zoom-in-75 motion-safe:duration-300">
            <DrawnItem drawn={result.drawn[0]} catalog={catalog} isLarge />
          </div>
        )}
        {result !== null && (
          <DialogFooter>
            {drawnGift && <Button onClick={() => onGive(drawnGift)}>{t.drawing.give}</Button>}
            {!drawing?.all && result.draws > 0 && (
              <Button variant="outline" onClick={onAgain}>
                {t.drawing.again(result.draws)}
              </Button>
            )}
            <Button variant={drawnGift ? "ghost" : "default"} onClick={onClose}>
              {t.drawing.keep}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}

/**
 * 抽到的一样东西：礼物是 `bg-muted` 圆角方块里的插画、名字、稀有度，稀有的插画角上一个闪光图标；
 * 重置卡是它的插画、「重置卡」、「很少见」。大的（单抽）稀有度写成 Badge，重置卡多一句说明。
 * 体验券是它的方块、券名、「体验券」，大的多一句它是做什么的。
 */
function DrawnItem({ drawn, catalog, isLarge = false }: { drawn: Drawn; catalog: Map<string, GiftInfo>; isLarge?: boolean }) {
  if (drawn.kind === "ticket") {
    const kind = copy.gifts.tickets.kinds[drawn.ticket];
    return (
      <div className={cn("flex flex-col items-center text-center", isLarge ? "gap-2" : "gap-1")}>
        <TicketTile kind={drawn.ticket} size={isLarge ? "large" : "fill"} />
        <p className={isLarge ? "text-header" : "text-sm"}>{kind.name}</p>
        {isLarge ? (
          <Badge variant="secondary">{t.drawing.ticket.rarity}</Badge>
        ) : (
          <p className="text-meta text-muted-foreground">{t.drawing.ticket.rarity}</p>
        )}
        {isLarge && <p className="text-subtitle text-muted-foreground">{kind.note}</p>}
      </div>
    );
  }
  const gift = drawn.kind === "gift" ? catalog.get(drawn.gift_id) : undefined;
  const name = gift ? gift.name : drawn.kind === "reset_card" ? t.drawing.resetCard.name : "";
  const rarity = gift ? t.rarity[gift.rarity] : t.drawing.resetCard.rarity;
  const isRare = gift?.rarity === "rare";
  return (
    <div className={cn("flex flex-col items-center text-center", isLarge ? "gap-2" : "gap-1")}>
      <GiftTile gift={gift} isResetCard={drawn.kind === "reset_card"} size={isLarge ? "large" : "fill"}>
        {isRare && <RareIcon className={cn("absolute text-foreground", isLarge ? "top-2 right-2 size-5" : "top-1 right-1 size-4")} />}
      </GiftTile>
      <p className={isLarge ? "text-header" : "text-sm"}>{name}</p>
      {isLarge && (isRare || !gift) ? (
        <Badge variant="secondary">{rarity}</Badge>
      ) : (
        <p className="text-meta text-muted-foreground">{rarity}</p>
      )}
      {isLarge && !gift && <p className="text-subtitle text-muted-foreground">{t.drawing.resetCard.note}</p>}
    </div>
  );
}

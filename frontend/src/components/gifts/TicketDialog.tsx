"use client";

import { useEffect, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerDescription, DrawerFooter, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import type { GiftInfo } from "@/lib/gifts";
import { readOutfitList, type OutfitCard } from "@/lib/standOutfit";
import {
  DATE_IDEAS,
  dayAhead,
  MAX_DAYS_AHEAD,
  redeemDate,
  redeemLetter,
  redeemLikedGift,
  redeemPhoto,
  type PhotoWish,
  type TicketKind,
} from "@/lib/tickets";
import { cn } from "@/lib/utils";

import { TicketTile } from "./Tickets";

const t = copy.gifts.tickets;

/** 用完以后的样子：说了什么，和去哪看。 */
export type TicketDone = { note: string; go: { label: string; href: string } | null };

/** 拍照券选的衣服：衣装库里的一套、自己描述、随她挑。 */
type Outfit = { kind: "card"; id: string; card: string; look: "everyday" | "stage" } | { kind: "describe" } | { kind: "free" };

/**
 * 用一张体验券（DESIGN.md「礼物页」）：宽屏对话框、窄屏底部面板。上面是这种券的方块和说明，下面选一位偶像
 * （小头像加名字的下拉框），拍照券再选穿什么（她衣装库里的卡，每张缩略图下面一个私服 / 演出服的切换；
 * 「随她挑」「自己描述」），邀约券再选一起做什么（几个常见的，也能自己写）和哪天（明天到一周以内）。
 * 「使用」以后转圈，用完换成一句结果和去哪看（信箱、她的对话）；没用成在框里说原因，券不扣。
 */
export function TicketDialog({
  kind,
  characters,
  catalog,
  isWide,
  timeZone,
  onClose,
  onDone,
  onGo,
  idea,
  herFirst,
}: {
  kind: TicketKind | null;
  characters: Character[];
  catalog: Map<string, GiftInfo>;
  isWide: boolean;
  timeZone: string;
  onClose: () => void;
  onDone: () => void;
  onGo: (href: string) => void;
  /** 邀约券：先填好的一起做什么（地图上选的地方，GLOSSARY.md「地图」）；没给是第一个常见的。 */
  idea?: string;
  /** 先选好的偶像；没给是名单里第一位。 */
  herFirst?: string;
}) {
  const [herId, setHerId] = useState<string>(herFirst ?? "");
  const [outfit, setOutfit] = useState<Outfit>({ kind: "free" });
  const [described, setDescribed] = useState("");
  const [what, setWhat] = useState<string>(idea ?? DATE_IDEAS[0]);
  const [ahead, setAhead] = useState(1);
  const [isUsing, setUsing] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [done, setDone] = useState<TicketDone | null>(null);
  const her = characters.find((c) => c.id === herId) ?? characters[0];

  // 换一种券（或在地图上换一处）打开时从头来。
  const opening = kind === null ? null : `${kind}|${idea ?? ""}|${herFirst ?? ""}`;
  const [openedFor, setOpenedFor] = useState<string | null>(null);
  if (opening !== openedFor) {
    setOpenedFor(opening);
    if (kind !== null) {
      setOutfit({ kind: "free" });
      setDescribed("");
      setWhat(idea ?? DATE_IDEAS[0]);
      if (herFirst) setHerId(herFirst);
      setAhead(1);
      setFailure(null);
      setDone(null);
    }
  }

  async function use() {
    if (!kind || !her) return;
    setUsing(true);
    setFailure(null);
    try {
      if (kind === "liked_gift") {
        const { gift_id } = await redeemLikedGift(her.id);
        setDone({ note: t.done.liked_gift(her.name, catalog.get(gift_id)?.name ?? gift_id), go: null });
      } else if (kind === "letter") {
        await redeemLetter(her.id);
        setDone({ note: t.done.letter(her.name), go: { label: t.goLetters, href: "/letters" } });
      } else if (kind === "photo") {
        const wish: PhotoWish =
          outfit.kind === "card"
            ? { outfit_id: outfit.id, card: outfit.card, look: outfit.look }
            : outfit.kind === "describe" && described.trim()
              ? { described: described.trim() }
              : {};
        await redeemPhoto(her.id, wish);
        setDone({ note: t.done.photo(her.name), go: chatOf(her) });
      } else if (kind === "date") {
        await redeemDate(her.id, what.trim(), dayAhead(new Date(), ahead, timeZone));
        setDone({ note: t.done.date(her.name), go: chatOf(her) });
      }
      onDone();
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setUsing(false);
    }
  }

  const canUse = !!her && !isUsing && (kind !== "date" || what.trim().length > 0);
  const title = kind ? t.kinds[kind].name : "";
  const description = kind ? t.kinds[kind].note : "";
  const body = kind && (
    <div className="flex flex-col gap-4">
      {done ? (
        <div className="flex flex-col items-center gap-3 py-4 text-center">
          <TicketTile kind={kind} size="large" />
          <p className="text-body">{done.note}</p>
        </div>
      ) : (
        <>
          <Labeled label={t.who}>
            <IdolSelect
              characters={characters}
              value={her?.id ?? ""}
              onChange={(id) => {
                setHerId(id);
                setOutfit({ kind: "free" }); // 她的衣装库换了
              }}
            />
          </Labeled>
          {kind === "photo" && her && (
            <OutfitChoice
              her={her}
              outfit={outfit}
              onOutfit={setOutfit}
              described={described}
              onDescribed={setDescribed}
            />
          )}
          {kind === "date" && (
            <>
              <Labeled label={t.what}>
                <div className="flex flex-wrap gap-2">
                  {DATE_IDEAS.map((idea) => (
                    <Button
                      key={idea}
                      size="sm"
                      variant={what === idea ? "secondary" : "outline"}
                      onClick={() => setWhat(idea)}
                    >
                      {idea}
                    </Button>
                  ))}
                </div>
                <Input
                  value={DATE_IDEAS.includes(what) ? "" : what}
                  placeholder={t.whatPlaceholder}
                  maxLength={30}
                  onChange={(e) => setWhat(e.target.value || DATE_IDEAS[0])}
                />
              </Labeled>
              <Labeled label={t.when}>
                <Select
                  items={days(timeZone).map((d) => ({ value: String(d.ahead), label: d.label }))}
                  value={String(ahead)}
                  onValueChange={(next) => setAhead(Number(next) || 1)}
                >
                  <SelectTrigger aria-label={t.when} className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {days(timeZone).map((d) => (
                      <SelectItem key={d.ahead} value={String(d.ahead)}>
                        {d.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </Labeled>
            </>
          )}
          {failure && <FailureNotice failure={failure} />}
        </>
      )}
    </div>
  );
  const actions = done ? (
    <>
      {done.go && (
        <Button
          onClick={() => {
            const href = done.go?.href;
            onClose();
            if (href) onGo(href);
          }}
        >
          {done.go.label}
        </Button>
      )}
      <Button variant={done.go ? "ghost" : "default"} onClick={onClose}>
        {copy.gifts.drawing.keep}
      </Button>
    </>
  ) : (
    <Button disabled={!canUse} onClick={use}>
      {isUsing && <Spinner data-icon="inline-start" />}
      {t.confirm}
    </Button>
  );
  const onOpenChange = (open: boolean) => !open && !isUsing && onClose();

  if (isWide) {
    return (
      <Dialog open={kind !== null} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          <div className="max-h-[60vh] overflow-y-auto">{body}</div>
          <DialogFooter>{actions}</DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={kind !== null} onOpenChange={onOpenChange}>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>{title}</DrawerTitle>
          <DrawerDescription>{description}</DrawerDescription>
        </DrawerHeader>
        <div className="max-h-[60vh] overflow-y-auto px-4">{body}</div>
        <DrawerFooter>{actions}</DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}

function chatOf(her: Character) {
  return { label: t.goChat, href: `/conversation/${encodeURIComponent(her.id)}` };
}

/** 明天起一周内每天一项：几天后和「明天（10月23日）」这样的写法。 */
export function days(timeZone: string) {
  const now = new Date();
  return Array.from({ length: MAX_DAYS_AHEAD }, (_, i) => {
    const ahead = i + 1;
    const [, month, day] = dayAhead(now, ahead, timeZone).split("-").map(Number);
    return { ahead, label: t.dayLabel(ahead, month, day) };
  });
}

export function Labeled({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2">
      <span className="text-meta text-muted-foreground">{label}</span>
      {children}
    </div>
  );
}

/** 选一位偶像：小头像加名字的下拉框。 */
export function IdolSelect({
  characters,
  value,
  onChange,
  label = t.who,
}: {
  characters: Character[];
  value: string;
  onChange: (id: string) => void;
  label?: string;
}) {
  const items = characters.map((c) => ({ value: c.id, label: c.name }));
  const show = (id: string) => {
    const her = characters.find((c) => c.id === id);
    return her ? (
      <span className="flex items-center gap-2">
        <MarkedAvatar name={her.name} src={her.avatar_url} size="sm" />
        {her.name}
      </span>
    ) : null;
  };
  return (
    <Select items={items} value={value} onValueChange={(next) => typeof next === "string" && onChange(next)}>
      <SelectTrigger aria-label={label} className="h-10 w-full">
        <SelectValue>{(current: string) => show(current)}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        {items.map((item) => (
          <SelectItem key={item.value} value={item.value}>
            {show(item.value)}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/**
 * 拍照券的穿什么：「随她挑」「自己描述」两个键，下面是她衣装库里的卡（缩略图和卡名，点了选中，
 * 选中的铺 `bg-muted`，下面私服 / 演出服切换，只有一套的不出切换）。她没有衣装库或者读不到时只有那两个键。
 */
function OutfitChoice({
  her,
  outfit,
  onOutfit,
  described,
  onDescribed,
}: {
  her: Character;
  outfit: Outfit;
  onOutfit: (outfit: Outfit) => void;
  described: string;
  onDescribed: (text: string) => void;
}) {
  const [cards, setCards] = useState<OutfitCard[] | null>(null);
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const listUrl = her.stand?.outfits_url ?? null;
  if (loadedFor !== her.id) {
    setLoadedFor(her.id);
    setCards(listUrl ? null : []);
  }
  useEffect(() => {
    if (!listUrl) return;
    let current = true;
    const absolute = new URL(listUrl, window.location.href).href;
    fetch(absolute)
      .then((res) => (res.ok ? res.json() : null))
      .then(
        (list) => current && setCards(list ? readOutfitList(list, absolute) : []),
        () => current && setCards([]),
      );
    return () => {
      current = false;
    };
  }, [listUrl]);

  return (
    <Labeled label={t.outfit}>
      <div className="flex gap-2">
        <Button
          size="sm"
          variant={outfit.kind === "free" ? "secondary" : "outline"}
          onClick={() => onOutfit({ kind: "free" })}
        >
          {t.outfitFree}
        </Button>
        <Button
          size="sm"
          variant={outfit.kind === "describe" ? "secondary" : "outline"}
          onClick={() => onOutfit({ kind: "describe" })}
        >
          {t.outfitDescribe}
        </Button>
      </div>
      {outfit.kind === "describe" && (
        <Input
          value={described}
          placeholder={t.describePlaceholder}
          maxLength={80}
          onChange={(e) => onDescribed(e.target.value)}
        />
      )}
      {cards === null ? (
        <p className="text-subtitle text-muted-foreground">{t.loadingOutfits}</p>
      ) : cards.length === 0 ? (
        <p className="text-subtitle text-muted-foreground">{t.noOutfits}</p>
      ) : (
        <ul className="grid grid-cols-3 gap-2 sm:grid-cols-4">
          {cards.map((card) => {
            const picked = outfit.kind === "card" && outfit.id.endsWith(`/${card.id}`);
            const look = picked && outfit.kind === "card" ? outfit.look : card.everyday ? "everyday" : "stage";
            const shown = (look === "stage" ? card.stage : card.everyday) ?? card.everyday ?? card.stage;
            const choose = (next: "everyday" | "stage") => {
              const chosen = next === "stage" ? card.stage : card.everyday;
              if (chosen) onOutfit({ kind: "card", id: chosen.id, card: card.name, look: next });
            };
            return (
              <li key={card.id} className={cn("flex flex-col gap-1 rounded-lg p-1.5", picked && "bg-muted")}>
                <button
                  type="button"
                  onClick={() => choose(look)}
                  className="flex flex-col gap-1 text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  {shown && (
                    <img
                      src={shown.thumbnail}
                      alt=""
                      loading="lazy"
                      className="aspect-square w-full rounded-md bg-muted object-contain"
                    />
                  )}
                  <span className="line-clamp-2 text-meta">{card.name}</span>
                </button>
                {picked && card.everyday && card.stage && (
                  <ToggleGroup
                    aria-label={card.name}
                    variant="segment"
                    size="sm"
                    spacing={0}
                    className="w-full"
                    value={[look]}
                    onValueChange={(value: unknown[]) => {
                      const next = value[0] as "everyday" | "stage" | undefined;
                      if (next) choose(next);
                    }}
                  >
                    <ToggleGroupItem value="everyday" className="flex-1">
                      {t.everyday}
                    </ToggleGroupItem>
                    <ToggleGroupItem value="stage" className="flex-1">
                      {t.stage}
                    </ToggleGroupItem>
                  </ToggleGroup>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </Labeled>
  );
}

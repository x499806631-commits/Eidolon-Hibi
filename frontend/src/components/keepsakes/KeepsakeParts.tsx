"use client";

import { useEffect, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Spinner } from "@/components/ui/spinner";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerDescription, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { getKeepsake, givenOn, keepsakeImageUrl, redrawKeepsake, removeKeepsake, type Keepsake } from "@/lib/keepsakes";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { Ja } from "@/theme/Ja";

const t = copy.keepsakes;

/**
 * 纪念品的图（GLOSSARY.md「纪念品」）：正方形；有图就是图，没有就是她壁纸色上的一个大 emoji，像一张小明信片。
 * 要在她的颜色子树里用（`herVars`）。
 */
export function KeepsakePicture({ keepsake, size = "tile" }: { keepsake: Keepsake; size?: "tile" | "large" }) {
  if (keepsake.has_image) {
    return (
      <img
        src={keepsakeImageUrl(keepsake)}
        alt={keepsake.name}
        className="aspect-square w-full rounded-lg bg-muted object-cover"
      />
    );
  }
  return (
    <div
      role="img"
      aria-label={keepsake.name}
      className={cn(
        "flex aspect-square w-full items-center justify-center rounded-lg bg-her-wall",
        size === "large" ? "text-7xl" : "text-4xl",
      )}
    >
      <span aria-hidden>{keepsake.emoji}</span>
    </div>
  );
}

/** 纪念品柜的一格：图和名字，整格可点。 */
export function KeepsakeTile({ keepsake, her, onOpen }: { keepsake: Keepsake; her: Character; onOpen: () => void }) {
  return (
    <li style={herVars(her.color)}>
      <button
        type="button"
        onClick={onOpen}
        aria-label={t.tileLabel(her.name, keepsake.name)}
        className="flex w-full flex-col gap-1.5 rounded-lg p-1.5 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="relative">
          <KeepsakePicture keepsake={keepsake} />
          {!keepsake.seen && <span aria-hidden className="absolute top-2 right-2 size-1.5 rounded-full bg-foreground" />}
        </span>
        <span className="line-clamp-2 text-subtitle">{keepsake.name}</span>
      </button>
    </li>
  );
}

/** 点开一件的详情：大图、她、缘由、日子、样子、她说的话。宽屏是对话框，窄屏是底部面板（DESIGN.md「Overlays」）。 */
export function KeepsakeDetail({
  keepsake,
  her,
  isWide,
  onClose,
  onChat,
  canDraw = false,
  onChanged,
  onRemoved,
}: {
  keepsake: Keepsake | null;
  her?: Character;
  isWide: boolean;
  onClose: () => void;
  onChat?: (her: Character) => void;
  /** 有生图时可以「重画这张图」（柜子里才给）。 */
  canDraw?: boolean;
  onChanged?: (keepsake: Keepsake) => void;
  /** 给了才有「拿出柜子」。 */
  onRemoved?: (id: number) => void;
}) {
  const open = keepsake !== null;
  const onOpenChange = (isOpen: boolean) => !isOpen && onClose();
  const body = keepsake && (
    <KeepsakeBody
      keepsake={keepsake}
      her={her}
      onChat={onChat}
      canDraw={canDraw}
      onChanged={onChanged}
      onRemoved={onRemoved}
    />
  );
  const title = keepsake?.name ?? "";
  const description = keepsake && her ? t.from(her.name) : "";
  if (isWide) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent style={her ? herVars(her.color) : undefined} className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>{description}</DialogDescription>
          </DialogHeader>
          {body}
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={open} onOpenChange={onOpenChange}>
      <DrawerContent style={her ? herVars(her.color) : undefined}>
        <DrawerHeader>
          <DrawerTitle>{title}</DrawerTitle>
          <DrawerDescription>{description}</DrawerDescription>
        </DrawerHeader>
        <div className="max-h-[70vh] overflow-y-auto px-4 pb-6">{body}</div>
      </DrawerContent>
    </Drawer>
  );
}

function KeepsakeBody({
  keepsake,
  her,
  onChat,
  canDraw,
  onChanged,
  onRemoved,
}: {
  keepsake: Keepsake;
  her?: Character;
  onChat?: (her: Character) => void;
  canDraw: boolean;
  onChanged?: (keepsake: Keepsake) => void;
  onRemoved?: (id: number) => void;
}) {
  const { year, month, day } = givenOn(keepsake);
  const [isRedrawing, setRedrawing] = useState(false);
  const [isConfirming, setConfirming] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const redraw = () => {
    setRedrawing(true);
    setFailure(null);
    redrawKeepsake(keepsake.id).then(
      (k) => onChanged?.(k),
      (e) => setFailure(toFailure(e)),
    ).finally(() => setRedrawing(false));
  };
  const remove = () => {
    removeKeepsake(keepsake.id).then(
      () => onRemoved?.(keepsake.id),
      (e) => setFailure(toFailure(e)),
    );
  };
  const tools = (canDraw && onChanged) || onRemoved;
  return (
    <div className="flex flex-col gap-4">
      <div className="mx-auto w-full max-w-72">
        <KeepsakePicture keepsake={keepsake} size="large" />
      </div>
      <div className="flex items-center gap-2 text-subtitle text-muted-foreground">
        {her && <MarkedAvatar name={her.name} src={her.avatar_url} size="sm" />}
        <span className="flex-1">{t.why(t.occasions[keepsake.occasion], keepsake.about)}</span>
        <span className="tabular-nums">{t.date(year, month, day)}</span>
      </div>
      {keepsake.look && <p className="text-body">{keepsake.look}</p>}
      <Said keepsake={keepsake} />
      {failure && <FailureNotice failure={failure} />}
      {(tools || (her && onChat)) && (
        <div className="flex flex-wrap items-center gap-2">
          {canDraw && onChanged && (
            <Button variant="outline" size="sm" disabled={isRedrawing} onClick={redraw}>
              {isRedrawing && <Spinner data-icon="inline-start" />}
              {isRedrawing ? t.redrawing : t.redraw}
            </Button>
          )}
          {onRemoved && (
            <Button variant="ghost" size="sm" disabled={isRedrawing} onClick={() => setConfirming(true)}>
              {t.remove}
            </Button>
          )}
          {her && onChat && (
            <Button variant="outline" size="sm" className="ml-auto" onClick={() => onChat(her)}>
              {t.goChat}
            </Button>
          )}
        </div>
      )}
      <AlertDialog open={isConfirming} onOpenChange={setConfirming}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.removeConfirm.title(keepsake.name)}</AlertDialogTitle>
            <AlertDialogDescription>{t.removeConfirm.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t.removeConfirm.cancel}</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={remove}>
              {t.removeConfirm.confirm}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

/** 她送的时候说的话：日语原文在上，中文在下。 */
function Said({ keepsake }: { keepsake: Keepsake }) {
  return (
    <div className="flex flex-col gap-2 rounded-lg bg-muted/60 p-3">
      <span className="text-meta text-muted-foreground">{t.said}</span>
      {keepsake.said.map((segment, i) => (
        <p key={i} className="flex flex-col gap-0.5">
          <span className="text-body">
            <Ja>{segment.ja}</Ja>
          </span>
          <span className="text-subtitle text-muted-foreground">{segment.zh}</span>
        </p>
      ))}
    </div>
  );
}

/**
 * 对话里、信里的礼物卡：左边小图，右边名字和她说的第一句中文；点了打开详情。
 * `id` 给的是哪件，自己去读；读不到就什么都不显示。
 */
export function KeepsakeCard({
  id,
  keepsake: given,
  her,
  isWide,
}: {
  id?: number;
  keepsake?: Keepsake | null;
  her: Character;
  isWide: boolean;
}) {
  const [keepsake, setKeepsake] = useState<Keepsake | null>(given ?? null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (given || id === undefined) return;
    let current = true;
    getKeepsake(id).then(
      (k) => current && setKeepsake(k),
      () => {}, // 读不到（已删）就不显示卡
    );
    return () => {
      current = false;
    };
  }, [id, given]);
  if (!keepsake) return null;
  const first = keepsake.said[0];
  return (
    <div style={herVars(her.color)}>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex w-full max-w-sm items-center gap-3 rounded-xl border bg-background p-2 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <span className="w-16 shrink-0">
          <KeepsakePicture keepsake={keepsake} />
        </span>
        <span className="flex min-w-0 flex-col gap-0.5">
          <span className="text-body font-medium">{keepsake.name}</span>
          {first && <span className="line-clamp-2 text-subtitle text-muted-foreground">{first.zh}</span>}
        </span>
      </button>
      <KeepsakeDetail keepsake={open ? keepsake : null} her={her} isWide={isWide} onClose={() => setOpen(false)} />
    </div>
  );
}

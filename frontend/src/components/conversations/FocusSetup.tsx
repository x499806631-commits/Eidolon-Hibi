"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Drawer, DrawerContent, DrawerDescription, DrawerFooter, DrawerHeader, DrawerTitle } from "@/components/ui/drawer";
import { Field, FieldDescription, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { copy } from "@/i18n";
import { clampMinutes, loadMinutes, MAX_MINUTES, MIN_MINUTES, rememberMinutes } from "@/lib/focusTimer";

const t = copy.conversation.focus;

/** 快捷的几档，分钟：点一下填进输入框，想要别的再自己改数字。 */
const PRESETS = [15, 25, 45, 60];

/**
 * 「一起专心」开始前问一句专心多久（DESIGN.md「Overlays」）：宽屏是对话框，窄屏是底部面板，
 * 和对话页同一个断点。几档快捷时长点一下填进输入框，也可以自己填 1–120 分钟；打开时填着上次选的。
 * 休息固定 5 分钟，不在这里选。
 */
export function FocusSetup({
  isOpen,
  onOpenChange,
  isWide,
  start,
}: {
  isOpen: boolean;
  onOpenChange: (isOpen: boolean) => void;
  /** 宽屏打开对话框，窄屏打开底部面板。 */
  isWide: boolean;
  /** 开始专心：多久（分钟，已经收进范围）。 */
  start: (minutes: number) => void;
}) {
  const [minutes, setMinutes] = useState("");
  // 每次打开都填上次选的，关掉时不清：关掉再打开还是它。
  useEffect(() => {
    if (isOpen) setMinutes(String(loadMinutes()));
  }, [isOpen]);

  const picked = Number(minutes);
  // 空着或填的不是数时按不下去，也不报错。
  const usable = minutes.trim() !== "" && Number.isFinite(picked) && picked >= MIN_MINUTES;
  const go = () => {
    if (!usable) return;
    const chosen = clampMinutes(picked);
    rememberMinutes(chosen);
    onOpenChange(false);
    start(chosen);
  };

  const body = (
    <>
      <Field>
        <FieldLabel htmlFor="focus-minutes">{t.minutesLabel}</FieldLabel>
        <Input
          id="focus-minutes"
          type="number"
          inputMode="numeric"
          min={MIN_MINUTES}
          max={MAX_MINUTES}
          value={minutes}
          onChange={(event) => setMinutes(event.target.value)}
          className="w-24"
        />
        <div className="flex flex-wrap gap-2 pt-1">
          {PRESETS.map((preset) => (
            <Button key={preset} variant="outline" size="sm" onClick={() => setMinutes(String(preset))}>
              {t.preset(String(preset))}
            </Button>
          ))}
        </div>
        <FieldDescription>{t.hint}</FieldDescription>
      </Field>
    </>
  );
  const actions = (
    <>
      <Button variant="outline" onClick={() => onOpenChange(false)}>
        {t.cancel}
      </Button>
      <Button onClick={go} disabled={!usable}>
        {t.go}
      </Button>
    </>
  );

  if (isWide) {
    return (
      <Dialog open={isOpen} onOpenChange={onOpenChange}>
        <DialogContent className="sm:max-w-sm">
          <DialogHeader>
            <DialogTitle>{t.title}</DialogTitle>
            <DialogDescription>{t.restHint}</DialogDescription>
          </DialogHeader>
          {body}
          <DialogFooter>{actions}</DialogFooter>
        </DialogContent>
      </Dialog>
    );
  }
  return (
    <Drawer open={isOpen} onOpenChange={onOpenChange} showSwipeHandle>
      <DrawerContent>
        <DrawerHeader>
          <DrawerTitle>{t.title}</DrawerTitle>
          <DrawerDescription>{t.restHint}</DrawerDescription>
        </DrawerHeader>
        <div className="px-4 pb-2">{body}</div>
        <DrawerFooter>{actions}</DrawerFooter>
      </DrawerContent>
    </Drawer>
  );
}

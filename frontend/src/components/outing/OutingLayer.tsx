"use client";

import { useEffect, useRef, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { Stand } from "@/components/conversations/stand/Stand";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import { photoImageUrl, type Character, type Outfit, type Segment } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { timeOfDay } from "@/lib/home";
import type { LiveConversation } from "@/lib/liveConversation";
import {
  ACTIONS,
  actionText,
  drawStop,
  endOuting,
  judgeEmotions,
  meet,
  nextStop,
  stopPictureUrl,
  takePhoto,
  type Outing,
  type OutingToday,
} from "@/lib/outing";
import { CALM, type StandDirection } from "@/lib/standAnimations";

import { cn } from "@/lib/utils";
import {
  EndOutingIcon,
  FoldOutingIcon,
  NextStopIcon,
  OtherActionIcon,
  OutingPhotoIcon,
  RedrawStopIcon,
  SendIcon,
} from "@/theme/icons";

import { OutingArt } from "./OutingArt";
import { useOutingTalk, type TalkLine } from "./useOutingTalk";

const t = copy.outing;
/** 一段台词至少说多久（毫秒），长的按字数加。 */
const LINE_MS = 1400;
const PER_CHAR_MS = 120;

/**
 * 约会的画面（GLOSSARY.md「约会」）：盖住她的对话页。背景是这一站（画好的图，没有就用代码画的插画），
 * 她的立绘站在中间，跟着每一段台词换表情、动嘴；底下是这次约会里最近几句、小动作和输入栏。
 * 上面一条写着在哪一站、第几站，右边是合照、下一站（最后一站是结束约会）和收起。
 * 说的话是这段对话的回合，收起回到对话页照样看得到；结束后换成她的回顾卡。
 */
export function OutingLayer({
  live,
  userId,
  character,
  standOutfits,
  today,
  isFullScreen,
  isReducedMotion,
  onClose,
  onChanged,
}: {
  /** 她的当前对话：约会结束时那里多一张约会卡，读一次。 */
  live: LiveConversation;
  userId: string;
  character: Character;
  standOutfits: readonly Outfit[];
  today: OutingToday;
  isFullScreen: boolean;
  isReducedMotion: boolean;
  onClose: () => void;
  /** 约会有了变化（见面、换站、结束）：页首那条约会提示跟着变。 */
  onChanged: (outing: Outing) => void;
}) {
  const [outing, setOuting] = useState<Outing | null>(today.outing);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [isWorking, setWorking] = useState(false);
  const [other, setOther] = useState<string | null>(null);
  const [wish, setWish] = useState<string | null>(null);
  const [direction, setDirection] = useState<StandDirection>(CALM);
  const [picture, setPicture] = useState<{ stop: string; v: number; shown: boolean } | null>(null);
  const [now] = useState(() => new Date());
  const [draft, setDraft] = useState("");
  const talk = useOutingTalk(outing?.conversation_id ?? null, userId);
  const busy = talk.isBusy;

  const changed = (next: Outing) => {
    setOuting(next);
    onChanged(next);
  };

  const run = async (work: () => Promise<void>) => {
    setWorking(true);
    setFailure(null);
    try {
      await work();
    } catch (e) {
      setFailure(toFailure(e));
    } finally {
      setWorking(false);
    }
  };

  // 这一站的背景：有生图就先试画好的那张，没画过就请后端画，画好换上；画不出来用插画。
  const here = outing ? outing.stops[Math.min(outing.stop, outing.stops.length - 1)] : null;
  const spot = outing?.spot ?? today.date?.spot ?? null;
  useEffect(() => {
    if (!outing || !here || !today.can_draw) return;
    let current = true;
    setPicture({ stop: here.id, v: 0, shown: false });
    drawStop(outing.id).then(
      ({ drawn }) => current && drawn && setPicture({ stop: here.id, v: Date.now(), shown: false }),
      () => {},
    );
    return () => {
      current = false;
    };
  }, [outing?.id, here?.id, today.can_draw]); // eslint-disable-line react-hooks/exhaustive-deps

  // 这次约会里的几句：约会那段对话的。
  const said = talk.lines;
  const lastReply = [...said].reverse().find((l): l is Extract<TalkLine, { mine: false }> => !l.mine);

  // 她每回一句，按段判断表情，一段一段地换脸、动嘴。
  const played = useRef<string | null>(null);
  useEffect(() => {
    if (!lastReply || lastReply.pending || played.current === lastReply.key) return;
    played.current = lastReply.key;
    const userSaid = [...said].reverse().find((l): l is Extract<TalkLine, { mine: true }> => l.mine);
    let cancelled = false;
    const segments: Segment[] = lastReply.segments;
    judgeEmotions(character.id, userSaid?.text ?? "", segments).then(
      async (judged) => {
        let nods = 0;
        for (const [i, segment] of segments.entries()) {
          if (cancelled) return;
          const j = judged[i];
          const nod = j?.nod ? { kind: j.nod, id: ++nods + Date.now() } : null;
          setDirection((d) => ({
            mood: j && j.emotion !== "calm" ? j.emotion : d.mood,
            speaking: !isReducedMotion,
            looking: true,
            nod,
          }));
          await new Promise((r) => setTimeout(r, Math.max(LINE_MS, segment.zh.length * PER_CHAR_MS)));
        }
        if (!cancelled) setDirection((d) => ({ ...d, speaking: false, nod: null }));
      },
      () => {},
    );
    return () => {
      cancelled = true;
    };
  }, [lastReply, said, character.id, isReducedMotion]);

  const send = (text: string) => {
    if (!text.trim() || busy) return;
    setDraft("");
    void talk.send(text);
  };

  const isLast = outing ? outing.stop >= outing.stops.length - 1 : false;
  const time = timeOfDay(now);
  const canPhoto = today.can_draw && outing !== null && outing.ended_at === null && outing.photos < outing.max_photos;

  const background =
    picture && picture.v > 0 && here ? (
      // eslint-disable-next-line @next/next/no-img-element -- 后端画好的背景
      <img
        src={stopPictureUrl(spot, here.id, picture.v)}
        alt=""
        className={cn("absolute inset-0 size-full object-cover transition-opacity duration-500", !picture.shown && "opacity-0")}
        onLoad={() => setPicture((p) => p && { ...p, shown: true })}
        onError={() => setPicture(null)}
      />
    ) : null;

  return (
    <div role="dialog" aria-label={t.label(character.name)} className="absolute inset-0 z-30 flex flex-col overflow-hidden bg-black text-white">
      <div className="absolute inset-0">
        <OutingArt spot={spot} stop={here?.id ?? "meet"} time={time} />
        {background}
      </div>
      {outing && (
        <div aria-hidden className={cn("pointer-events-none absolute inset-x-0 bottom-0", isFullScreen ? "h-[78%]" : "top-14")}>
          <Stand
            outfits={standOutfits}
            framing={isFullScreen ? "knees" : "waist"}
            isPaused={false}
            entrance={isFullScreen && !isReducedMotion ? "rise" : "fade"}
            direction={direction}
            isReducedMotion={isReducedMotion}
          />
        </div>
      )}
      <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-[55%] bg-linear-to-b from-transparent via-black/55 to-black/85" />

      <header className="relative flex items-center gap-2 bg-black/35 px-3 py-2 backdrop-blur-sm">
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-medium">{here ? t.stopTitle(here.name) : t.title(character.name)}</p>
          {outing && (
            <p className="text-xs text-white/75">
              {t.progress(outing.stop + 1, outing.stops.length)} · {outing.what}
            </p>
          )}
        </div>
        {outing && today.can_draw && outing.ended_at === null && (
          <Button
            size="icon-sm"
            variant="ghost"
            className="text-white hover:bg-white/15 hover:text-white"
            aria-label={t.redraw}
            disabled={isWorking}
            onClick={() =>
              run(async () => {
                if (!here) return;
                await drawStop(outing.id, true);
                setPicture({ stop: here.id, v: Date.now(), shown: false });
              })
            }
          >
            <RedrawStopIcon />
          </Button>
        )}
        {canPhoto && (
          <Button
            size="sm"
            variant="ghost"
            className="text-white hover:bg-white/15 hover:text-white"
            onClick={() => setWish((w) => (w === null ? "" : null))}
          >
            <OutingPhotoIcon />
            {t.photo(outing.max_photos - outing.photos)}
          </Button>
        )}
        <Button
          size="icon-sm"
          variant="ghost"
          className="text-white hover:bg-white/15 hover:text-white"
          aria-label={t.fold}
          onClick={onClose}
        >
          <FoldOutingIcon />
        </Button>
      </header>

      <div className="relative flex-1" />

      <div className="relative flex flex-col gap-2 px-3 pb-3">
        {(failure ?? talk.failure) && <FailureNotice failure={(failure ?? talk.failure)!} />}
        {!outing ? (
          <div className="mx-auto flex w-full max-w-md flex-col items-center gap-3 rounded-2xl bg-black/55 p-4 text-center">
            <p className="text-body">{today.date ? t.meetPrompt(character.name, today.date.what) : t.noDate}</p>
            {today.date && (
              <p className="text-xs text-white/75">
                {t.window(today.date.best_from, today.date.best_until, today.date.closes)}
              </p>
            )}
            <Button
              variant="her"
              disabled={!today.date?.can_start || isWorking}
              onClick={() =>
                run(async () => {
                  changed(await meet(character.id));
                })
              }
            >
              {isWorking && <Spinner data-icon="inline-start" />}
              {t.meet}
            </Button>
          </div>
        ) : outing.ended_at ? (
          <div className="mx-auto flex w-full max-w-md flex-col gap-3 rounded-2xl bg-black/60 p-4">
            <p className="text-sm font-medium">{t.over(character.name)}</p>
            {outing.recap && <p className="text-body leading-relaxed">{outing.recap}</p>}
            <Button variant="her" onClick={onClose}>
              {t.backToChat}
            </Button>
          </div>
        ) : (
          <>
            <ol aria-live="polite" className="flex max-h-[30vh] flex-col gap-1.5 overflow-y-auto">
              {said.slice(-4).map((l) =>
                l.mine ? (
                  <li key={l.key} className="self-end rounded-2xl bg-white/85 px-3 py-1.5 text-sm text-black">
                    {l.text}
                  </li>
                ) : (
                  <li key={l.key} className="max-w-[90%] self-start rounded-2xl bg-black/60 px-3 py-2 text-sm">
                    {l.pending ? (
                      <Spinner className="size-4" />
                    ) : (
                      l.segments.map((s, i) => (
                        <p key={i} className="leading-relaxed">
                          {s.zh}
                        </p>
                      ))
                    )}
                    {l.photo?.status === "ready" && (
                      // eslint-disable-next-line @next/next/no-img-element -- 她发来的合照
                      <img src={photoImageUrl(l.photo.id)} alt={t.photoAlt} className="mt-2 max-h-48 rounded-xl" />
                    )}
                    {l.photo?.status === "pending" && <p className="mt-1 text-xs text-white/70">{t.photoDrawing}</p>}
                  </li>
                ),
              )}
            </ol>
            {wish !== null && (
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  void run(async () => {
                    changed(await takePhoto(outing.id, wish));
                    setWish(null);
                    await talk.reload();
                  });
                }}
              >
                <Input
                  value={wish}
                  maxLength={60}
                  placeholder={t.wishPlaceholder}
                  onChange={(e) => setWish(e.target.value)}
                  className="flex-1 bg-white/90 text-black"
                />
                <Button type="submit" variant="her" disabled={isWorking}>
                  {isWorking ? <Spinner /> : <OutingPhotoIcon />}
                  {t.shoot}
                </Button>
              </form>
            )}
            <div className="flex flex-wrap gap-1.5">
              {ACTIONS.map((action) => (
                <Button
                  key={action}
                  size="sm"
                  variant="secondary"
                  className="bg-white/85 text-black hover:bg-white"
                  disabled={busy}
                  onClick={() => send(actionText(action))}
                >
                  {action}
                </Button>
              ))}
              <Button
                size="sm"
                variant="secondary"
                className="bg-white/85 text-black hover:bg-white"
                onClick={() => setOther((o) => (o === null ? "" : null))}
              >
                <OtherActionIcon />
                {t.other}
              </Button>
            </div>
            {other !== null && (
              <form
                className="flex gap-2"
                onSubmit={(e) => {
                  e.preventDefault();
                  if (!other.trim()) return;
                  send(actionText(other));
                  setOther(null);
                }}
              >
                <Input
                  value={other}
                  maxLength={60}
                  autoFocus
                  placeholder={t.otherPlaceholder}
                  onChange={(e) => setOther(e.target.value)}
                  className="flex-1 bg-white/90 text-black"
                />
                <Button type="submit" variant="her" disabled={busy || !other.trim()}>
                  {t.doIt}
                </Button>
              </form>
            )}
            <form
              className="flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                send(draft);
              }}
            >
              <Input
                value={draft}
                placeholder={t.placeholder}
                aria-label={t.placeholder}
                onChange={(e) => setDraft(e.target.value)}
                className="flex-1 bg-white/90 text-black"
              />
              <Button type="submit" variant="her" size="icon" aria-label={copy.conversation.send} disabled={busy || !draft.trim()}>
                {busy ? <Spinner /> : <SendIcon />}
              </Button>
            </form>
            <div className="flex justify-end gap-2">
              {isLast ? (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={isWorking || busy}
                  onClick={() =>
                    run(async () => {
                      changed(await endOuting(outing.id));
                      live.reread();
                    })
                  }
                >
                  {isWorking ? <Spinner /> : <EndOutingIcon />}
                  {t.end}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={isWorking || busy}
                  onClick={() => run(async () => changed(await nextStop(outing.id)))}
                >
                  <NextStopIcon />
                  {t.next(outing.stops[outing.stop + 1]?.name ?? "")}
                </Button>
              )}
              {!isLast && (
                <Button
                  size="sm"
                  variant="ghost"
                  className="text-white hover:bg-white/15 hover:text-white"
                  disabled={isWorking || busy}
                  onClick={() =>
                    run(async () => {
                      changed(await endOuting(outing.id));
                      live.reread();
                    })
                  }
                >
                  {t.endEarly}
                </Button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

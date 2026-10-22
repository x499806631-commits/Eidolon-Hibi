"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode, type TouchEvent } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { copy } from "@/i18n";
import type { Character, Segment } from "@/lib/api";
import {
  getDiary,
  isUnread,
  monthDay,
  neighbours,
  preview,
  readEntry,
  unreadDiary,
  type DiaryEntry,
} from "@/lib/diary";
import { toFailure, type Failure } from "@/lib/failure";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { BackIcon, DiaryClosedIcon, DiaryIcon, DiarySecretIcon, ForwardIcon } from "@/theme/icons";
import { Ja } from "@/theme/Ja";

const t = copy.diary;

/**
 * 日记页（`/diary`）：导航外壳里一栏，版式同信箱页（内容最宽 `max-w-3xl`、居中）。
 * 清单一行一篇，最新的日子在上：最左一条她印象色的书脊，她的头像、名字、第一段的中文（单行截断）、日子；能看又没读过的名字加粗、行尾一个小点。
 * 合上的那篇（她冷淡、疏远或冷却中）露出一把锁和「她把日记本合上了」。
 * 点开换成日记纸：她的壁纸色铺底，每段日语原文下面是中文翻译（可以收起只看原文），右下角那一天；
 * 只给看开头时，下面一句「只让你看到这里」；有一页没给看时「最后一页被她用夹子夹住了」；给看了那一页时，
 * 它另起在一块虚线框里，上面一个回形针和「她没打算给你看的一页」。下面「回到日记」和「去找她」。打开就算读过。
 * 翻页：日记纸两边各一个 ghost 图标键，翻到同一位偶像的前一天、后一天（没有就不显示），键盘 ← →、手机左右滑也能翻；
 * 新的一页像书页一样从翻过来的那一边转进来，减少动态效果时只淡入。翻到的那一页同样算读过。
 */
export function DiaryPage() {
  const [entries, setEntries] = useState<DiaryEntry[] | null>(null);
  const [open, setOpen] = useState<DiaryEntry | null>(null);
  const [turned, setTurned] = useState<Turn>("none");
  const [failure, setFailure] = useState<Failure | null>(null);
  const { characters, go } = useDirectory();
  const byId = useMemo(() => new Map((characters ?? []).map((c) => [c.id, c])), [characters]);

  useEffect(() => {
    let current = true;
    getDiary().then(
      (e) => current && setEntries(e),
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, []);

  function show(entry: DiaryEntry, turn: Turn = "none") {
    setOpen(entry);
    setTurned(turn);
    if (!isUnread(entry)) return;
    readEntry(entry.id).then(
      (read) => {
        unreadDiary.read();
        setEntries((all) => all?.map((e) => (e.id === read.id ? read : e)) ?? all);
      },
      () => {}, // 没记上已读也能看，下次打开再记
    );
  }

  const her = open ? byId.get(open.character_id) : undefined;
  const pages = open && entries ? neighbours(entries, open) : { previous: null, next: null };
  const turnTo = (turn: "previous" | "next") => {
    const to = pages[turn];
    if (to) show(to, turn);
  };
  return (
    <PageShell width="full" isInShell header={<h1 className="text-list-title">{t.title}</h1>}>
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
        {failure && <FailureNotice failure={failure} />}
        {open ? (
          <>
            <div>
              <Button variant="ghost" size="sm" onClick={() => setOpen(null)}>
                <BackIcon />
                {t.back}
              </Button>
            </div>
            <Pages
              entry={open}
              her={her}
              turned={turned}
              previous={pages.previous}
              next={pages.next}
              onTurn={turnTo}
            />
            {her && (
              <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={() => go(`/conversation/${encodeURIComponent(her.id)}`)}>
                  {t.goChat}
                </Button>
              </div>
            )}
          </>
        ) : entries === null || characters === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : entries.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <DiaryIcon />
              </EmptyMedia>
              <EmptyTitle>{t.empty}</EmptyTitle>
              <EmptyDescription>{t.emptyHint}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="flex flex-col">
            {entries.map((entry) => {
              const c = byId.get(entry.character_id);
              return c ? <EntryRow key={entry.id} entry={entry} her={c} onOpen={() => show(entry)} /> : null;
            })}
          </ul>
        )}
      </div>
    </PageShell>
  );
}

/** 清单的一行，整行可点。 */
function EntryRow({ entry, her, onOpen }: { entry: DiaryEntry; her: Character; onOpen: () => void }) {
  const { month, day } = monthDay(entry);
  const date = t.date(month, day);
  const unread = isUnread(entry);
  const closed = entry.shown === "closed";
  return (
    <li style={herVars(her.color)} className="border-b last:border-b-0">
      <button
        type="button"
        onClick={onOpen}
        aria-label={t.rowLabel(her.name, date, unread)}
        className="flex w-full items-center gap-3 rounded-lg px-2 py-3 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        {/* 她那本日记的书脊：她的印象色 */}
        <span aria-hidden className="w-1 self-stretch rounded-full bg-her-button" />
        <MarkedAvatar name={her.name} src={her.avatar_url} size="list" />
        <span className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className={cn("text-body", unread && "font-medium")}>{her.name}</span>
          <span className="flex min-w-0 items-center gap-1 text-subtitle text-muted-foreground">
            {closed && <DiaryClosedIcon aria-hidden className="size-3.5 shrink-0" />}
            <span className="truncate">{closed ? t.closedRow : preview(entry)}</span>
          </span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-meta text-muted-foreground tabular-nums">{date}</span>
          <span aria-hidden className={cn("size-1.5 rounded-full", unread ? "bg-foreground" : "bg-transparent")} />
        </span>
      </button>
    </li>
  );
}

/** 怎么翻到这一页的：从清单打开、往前翻、往后翻。 */
type Turn = "none" | "previous" | "next";

const SWIPE_PX = 50;

/** 日记纸和两边的翻页键；键盘 ← →、手机左右滑也能翻。新的一页按翻的方向转进来。 */
function Pages({
  entry,
  her,
  turned,
  previous,
  next,
  onTurn,
}: {
  entry: DiaryEntry;
  her?: Character;
  turned: Turn;
  previous: DiaryEntry | null;
  next: DiaryEntry | null;
  onTurn: (turn: "previous" | "next") => void;
}) {
  const touch = useRef<{ x: number; y: number } | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest("input, textarea, [contenteditable='true']")) return;
      if (event.key === "ArrowLeft") onTurn("previous");
      if (event.key === "ArrowRight") onTurn("next");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onTurn]);

  const onTouchStart = (event: TouchEvent) => {
    const t0 = event.touches[0];
    touch.current = t0 ? { x: t0.clientX, y: t0.clientY } : null;
  };
  const onTouchEnd = (event: TouchEvent) => {
    const start = touch.current;
    const t1 = event.changedTouches[0];
    touch.current = null;
    if (!start || !t1) return;
    const dx = t1.clientX - start.x;
    if (Math.abs(dx) < SWIPE_PX || Math.abs(dx) < Math.abs(t1.clientY - start.y)) return;
    onTurn(dx > 0 ? "previous" : "next");
  };

  const dateOf = (e: DiaryEntry) => {
    const { month, day } = monthDay(e);
    return t.date(month, day);
  };
  return (
    <div className="flex items-center gap-1 md:gap-2">
      <TurnButton to={previous} label={previous ? t.previous(dateOf(previous)) : ""} onTurn={() => onTurn("previous")}>
        <BackIcon />
      </TurnButton>
      <div className="min-w-0 flex-1 perspective-distant" onTouchStart={onTouchStart} onTouchEnd={onTouchEnd}>
        <div
          key={entry.id}
          className={cn(
            turned === "next" && "origin-left motion-safe:animate-page-next motion-reduce:animate-page-fade",
            turned === "previous" && "origin-right motion-safe:animate-page-previous motion-reduce:animate-page-fade",
          )}
        >
          <DiaryPaper entry={entry} her={her} />
        </div>
      </div>
      <TurnButton to={next} label={next ? t.next(dateOf(next)) : ""} onTurn={() => onTurn("next")}>
        <ForwardIcon />
      </TurnButton>
    </div>
  );
}

/** 翻页键：没有那一页时占着位置但看不见、点不到，好让日记纸不左右跳。 */
function TurnButton({
  to,
  label,
  onTurn,
  children,
}: {
  to: DiaryEntry | null;
  label: string;
  onTurn: () => void;
  children: ReactNode;
}) {
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={label || undefined}
      aria-hidden={to ? undefined : true}
      tabIndex={to ? undefined : -1}
      disabled={!to}
      onClick={onTurn}
      className={cn("shrink-0", !to && "invisible")}
    >
      {children}
    </Button>
  );
}

/** 日记纸：她的壁纸色铺底；能看的段落，没给看的地方说一句，给看的那一页另起在虚线框里。 */
function DiaryPaper({ entry, her }: { entry: DiaryEntry; her?: Character }) {
  const [translated, setTranslated] = useState(true);
  const { month, day } = monthDay(entry);
  const name = her?.name ?? "";
  return (
    <article
      style={her ? herVars(her.color) : undefined}
      className={cn("flex flex-col gap-5 rounded-xl p-6 md:p-10", her ? "bg-her-wall" : "bg-muted")}
    >
      {entry.shown === "closed" ? (
        <div className="flex flex-col items-center gap-2 py-8 text-center text-muted-foreground">
          <DiaryClosedIcon aria-hidden className="size-6" />
          <p className="text-body">{t.closed(name)}</p>
        </div>
      ) : (
        <>
          <div className="flex justify-end">
            <Button variant="ghost" size="sm" onClick={() => setTranslated((v) => !v)}>
              {translated ? t.hideTranslation : t.showTranslation}
            </Button>
          </div>
          {entry.segments.map((s, i) => (
            <Paragraph key={i} segment={s} translated={translated} />
          ))}
          {entry.more && <p className="text-subtitle text-muted-foreground">{t.opening(name)}</p>}
          {entry.secret_hidden && (
            <p className="flex items-center gap-1.5 text-subtitle text-muted-foreground">
              <DiarySecretIcon aria-hidden className="size-3.5" />
              {t.secretHidden(name)}
            </p>
          )}
          {entry.secret.length > 0 && (
            <section className="flex flex-col gap-3 rounded-lg border border-dashed border-foreground/30 p-4">
              <h2 className="flex items-center gap-1.5 text-meta text-muted-foreground">
                <DiarySecretIcon aria-hidden className="size-3.5" />
                {t.secretTitle}
              </h2>
              {entry.secret.map((s, i) => (
                <Paragraph key={i} segment={s} translated={translated} />
              ))}
            </section>
          )}
        </>
      )}
      <p className="text-right text-meta text-muted-foreground tabular-nums">{t.date(month, day)}</p>
    </article>
  );
}

/** 一段：日语原文，下面是中文翻译；翻译和原文一样时不再写一遍。 */
function Paragraph({ segment, translated }: { segment: Segment; translated: boolean }) {
  const ja = segment.ja.trim();
  const zh = segment.zh.trim();
  return (
    <div className="flex flex-col gap-1">
      <p className="text-body leading-relaxed whitespace-pre-line">
        <Ja>{ja}</Ja>
      </p>
      {translated && zh && zh !== ja && <p className="text-subtitle text-muted-foreground">{zh}</p>}
    </div>
  );
}

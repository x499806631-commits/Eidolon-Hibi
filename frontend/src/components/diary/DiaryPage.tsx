"use client";

import { useEffect, useMemo, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { copy } from "@/i18n";
import type { Character, Segment } from "@/lib/api";
import { getDiary, isUnread, monthDay, preview, readEntry, unreadDiary, type DiaryEntry } from "@/lib/diary";
import { toFailure, type Failure } from "@/lib/failure";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { BackIcon, DiaryClosedIcon, DiaryIcon, DiarySecretIcon } from "@/theme/icons";
import { Ja } from "@/theme/Ja";

const t = copy.diary;

/**
 * 日记页（`/diary`）：导航外壳里一栏，版式同信箱页（内容最宽 `max-w-3xl`、居中）。
 * 清单一行一篇，最新的日子在上：她的头像、名字、第一段的中文（单行截断）、日子；能看又没读过的名字加粗、行尾一个小点。
 * 合上的那篇（她冷淡、疏远或冷却中）露出一把锁和「她把日记本合上了」。
 * 点开换成日记纸：她的壁纸色铺底，每段日语原文下面是中文翻译（可以收起只看原文），右下角那一天；
 * 只给看开头时，下面一句「只让你看到这里」；有一页没给看时「最后一页被她用夹子夹住了」；给看了那一页时，
 * 它另起在一块虚线框里，上面一个回形针和「她没打算给你看的一页」。下面「回到日记」和「去找她」。打开就算读过。
 */
export function DiaryPage() {
  const [entries, setEntries] = useState<DiaryEntry[] | null>(null);
  const [open, setOpen] = useState<DiaryEntry | null>(null);
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

  function show(entry: DiaryEntry) {
    setOpen(entry);
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
            <DiaryPaper entry={open} her={her} />
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
    <li className="border-b last:border-b-0">
      <button
        type="button"
        onClick={onOpen}
        aria-label={t.rowLabel(her.name, date, unread)}
        className="flex w-full items-center gap-3 rounded-lg px-2 py-3 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
      >
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

"use client";

import { useEffect, useMemo, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { useProducerTimezone } from "@/components/conversations/useProducerTimezone";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { copy } from "@/i18n";
import type { Character, Segment } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { deliveredAt, getLetters, parts, preview, readLetter, unreadLetters, type Letter } from "@/lib/letters";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";
import { BackIcon, LettersIcon } from "@/theme/icons";
import { Ja } from "@/theme/Ja";

const t = copy.letters;

/**
 * 信箱页（`/letters`）：导航外壳里一栏，版式同日历页（内容最宽 `max-w-3xl`、居中）。
 * 清单一行一封信，最新的在上：她的头像、名字、正文第一句（单行截断）、送到的日子；没读过的名字加粗、行尾一个小点。
 * 点开一封换成信纸：她的壁纸色铺底，称呼、正文、落款，每段日语原文下面是中文翻译（可以收起只看原文），
 * 右下角送到的日子和时刻；下面「回到信箱」和「去找她」。打开就算读过。日子按制作人的时区。
 */
export function LettersPage() {
  const timeZone = useProducerTimezone();
  const [letters, setLetters] = useState<Letter[] | null>(null);
  const [open, setOpen] = useState<Letter | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const { characters, go } = useDirectory();
  const byId = useMemo(() => new Map((characters ?? []).map((c) => [c.id, c])), [characters]);

  useEffect(() => {
    let current = true;
    getLetters().then(
      (l) => current && setLetters(l),
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, []);

  function show(letter: Letter) {
    setOpen(letter);
    if (letter.read_at !== null) return;
    readLetter(letter.id).then(
      (read) => {
        unreadLetters.read();
        setLetters((all) => all?.map((l) => (l.id === read.id ? read : l)) ?? all);
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
            <LetterPaper letter={open} her={her} timeZone={timeZone} />
            {her && (
              <div className="flex justify-end">
                <Button variant="outline" size="sm" onClick={() => go(`/conversation/${encodeURIComponent(her.id)}`)}>
                  {t.goChat}
                </Button>
              </div>
            )}
          </>
        ) : letters === null || characters === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : letters.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <LettersIcon />
              </EmptyMedia>
              <EmptyTitle>{t.empty}</EmptyTitle>
              <EmptyDescription>{t.emptyHint}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <ul className="flex flex-col">
            {letters.map((letter) => {
              const c = byId.get(letter.character_id);
              return c ? (
                <LetterRow key={letter.id} letter={letter} her={c} timeZone={timeZone} onOpen={() => show(letter)} />
              ) : null;
            })}
          </ul>
        )}
      </div>
    </PageShell>
  );
}

/** 清单的一行，整行可点。 */
function LetterRow({
  letter,
  her,
  timeZone,
  onOpen,
}: {
  letter: Letter;
  her: Character;
  timeZone: string;
  onOpen: () => void;
}) {
  const { month, day } = deliveredAt(letter, timeZone);
  const date = t.date(month, day);
  const unread = letter.read_at === null;
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
          <span className="flex items-baseline gap-2">
            <span className={cn("text-body", unread && "font-medium")}>{her.name}</span>
            {letter.occasion !== "ordinary" && (
              <span className="text-meta text-muted-foreground">{t.occasions[letter.occasion]}</span>
            )}
          </span>
          <span className="truncate text-subtitle text-muted-foreground">{preview(letter)}</span>
        </span>
        <span className="flex shrink-0 flex-col items-end gap-1">
          <span className="text-meta text-muted-foreground tabular-nums">{date}</span>
          <span aria-hidden className={cn("size-1.5 rounded-full", unread ? "bg-foreground" : "bg-transparent")} />
        </span>
      </button>
    </li>
  );
}

/** 信纸：她的壁纸色铺底；称呼靠左，落款靠右，右下角送到的日子和时刻。 */
function LetterPaper({ letter, her, timeZone }: { letter: Letter; her?: Character; timeZone: string }) {
  const [translated, setTranslated] = useState(true);
  const { greeting, body, signature } = parts(letter);
  const { month, day, time } = deliveredAt(letter, timeZone);
  return (
    <article
      style={her ? herVars(her.color) : undefined}
      className={cn("flex flex-col gap-5 rounded-xl p-6 md:p-10", her ? "bg-her-wall" : "bg-muted")}
    >
      <div className="flex justify-end">
        <Button variant="ghost" size="sm" onClick={() => setTranslated((v) => !v)}>
          {translated ? t.hideTranslation : t.showTranslation}
        </Button>
      </div>
      {greeting && <Paragraph segment={greeting} translated={translated} />}
      {body.map((s, i) => (
        <Paragraph key={i} segment={s} translated={translated} />
      ))}
      {signature && <Paragraph segment={signature} translated={translated} isEnd />}
      <p className="text-right text-meta text-muted-foreground tabular-nums">{t.stamp(t.date(month, day), time)}</p>
    </article>
  );
}

/** 一段：日语原文，下面是中文翻译；翻译和原文一样（落款的名字）时不再写一遍。 */
function Paragraph({ segment, translated, isEnd }: { segment: Segment; translated: boolean; isEnd?: boolean }) {
  const ja = segment.ja.trim();
  const zh = segment.zh.trim();
  return (
    <div className={cn("flex flex-col gap-1", isEnd && "items-end text-right")}>
      <p className="text-body leading-relaxed whitespace-pre-line">
        <Ja>{ja}</Ja>
      </p>
      {translated && zh && zh !== ja && <p className="text-subtitle text-muted-foreground">{zh}</p>}
    </div>
  );
}

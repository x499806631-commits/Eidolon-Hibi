"use client";

import { useCallback, useEffect, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PROSE_WIDTH } from "@/components/app/SettingsSection";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { SettingsPane } from "@/components/settings/SettingsPane";
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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Empty, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import { copy } from "@/i18n";
import {
  ApiError,
  deleteExperience,
  type Experience,
  keepExperience,
  listDueSoonExperiences,
  listExperiences,
  pinExperience,
} from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { cn } from "@/lib/utils";
import { ExternalLinkIcon, PinIcon, TrashIcon, UnpinIcon } from "@/theme/icons";

const t = copy.experiences;

/** 「不再提示」记在这台设备上，和「联网」的开关一样（lib/webToggle）。 */
const NOTICE_HIDDEN = "eidolon.experiences.noticeHidden";

function noticeHidden(): boolean {
  try {
    return window.localStorage.getItem(NOTICE_HIDDEN) === "1";
  } catch {
    return false;
  }
}

function rememberNoticeHidden(): void {
  try {
    window.localStorage.setItem(NOTICE_HIDDEN, "1");
  } catch {
    // 存不下就每次显示，不影响别的
  }
}

/**
 * 设置「她们的经历」（GLOSSARY.md「她们的经历」）：她们自己在聊天里说做过的事。
 * 两级：先是头像，点谁看谁的；快到期限的跨角色列在最上面（通知栏可以关掉，关了之后用那一行按钮进来）。
 * 一条快到期限（剩 7 天以内）时挂红点、写「还有 N 天忘掉」；点文字就再记 30 天，另有永久保留和删掉。
 */
export function ExperiencesSettings() {
  const characters = useDirectory().characters ?? [];
  const [chosen, setChosen] = useState<string | null>(null);
  const [hers, setHers] = useState<Experience[] | null>(null);
  const [due, setDue] = useState<Experience[] | null>(null);
  const [showingDue, setShowingDue] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [busy, setBusy] = useState<Set<number>>(new Set());
  // 要删的那条。确认框关上时只把 confirmingDelete 置成 false，pendingDelete 留着，关闭动画里的文字不会闪成空白。
  const [pendingDelete, setPendingDelete] = useState<Experience | null>(null);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  useEffect(() => setHidden(noticeHidden()), []);
  useEffect(() => {
    listDueSoonExperiences().then(setDue, () => {}); // 通知栏不是要紧的事，取不到就不显示
  }, []);

  const load = useCallback((id: string) => {
    setHers(null);
    listExperiences(id).then(setHers, (e) => setFailure(toFailure(e)));
  }, []);

  useEffect(() => {
    if (chosen) load(chosen);
  }, [chosen, load]);

  const withBusy = async (id: number, work: () => Promise<void>) => {
    setBusy((ids) => new Set(ids).add(id));
    setFailure(null);
    try {
      await work();
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) setFailure(toFailure(e));
    } finally {
      setBusy((ids) => {
        const rest = new Set(ids);
        rest.delete(id);
        return rest;
      });
    }
  };

  const keep = (one: Experience) =>
    withBusy(one.id, async () => {
      await keepExperience(one.id);
      const fresh = { ...one, days_left: 30, due_soon: false, pinned: one.pinned };
      setHers((list) => list?.map((e) => (e.id === one.id ? fresh : e)) ?? null);
      setDue((list) => list?.filter((e) => e.id !== one.id) ?? null);
    });

  const pin = (one: Experience, pinned: boolean) =>
    withBusy(one.id, async () => {
      await pinExperience(one.id, pinned);
      const fresh = { ...one, pinned, days_left: pinned ? null : one.days_left, due_soon: false };
      setHers((list) => list?.map((e) => (e.id === one.id ? fresh : e)) ?? null);
      setDue((list) => list?.filter((e) => e.id !== one.id) ?? null);
    });

  const remove = (one: Experience) =>
    withBusy(one.id, async () => {
      setConfirmingDelete(false);
      await deleteExperience(one.id);
      setHers((list) => list?.filter((e) => e.id !== one.id) ?? null);
      setDue((list) => list?.filter((e) => e.id !== one.id) ?? null);
    });

  const nameOf = (id: string) => characters.find((c) => c.id === id)?.name ?? t.goneCharacter;
  const avatarOf = (id: string) => characters.find((c) => c.id === id)?.avatar_url;
  const count = due?.length ?? 0;

  return (
    <SettingsPane
      category="experiences"
      end={
        chosen ? (
          <Button variant="ghost" size="sm" onClick={() => { setChosen(null); setShowingDue(false); }}>
            {t.backToCharacters}
          </Button>
        ) : showingDue ? (
          <Button variant="ghost" size="sm" onClick={() => setShowingDue(false)}>
            {t.backToCharacters}
          </Button>
        ) : undefined
      }
    >
      <div className={cn(PROSE_WIDTH, "flex flex-col gap-6")}>
        <p className="text-subtitle text-muted-foreground">{t.intro}</p>

        {failure && <FailureNotice failure={failure} />}

        {count > 0 && !hidden && !showingDue && !chosen && (
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border px-3 py-2">
            <span className="text-body">{t.dueNotice(count)}</span>
            <span className="flex items-center gap-1">
              <Button variant="ghost" size="sm" onClick={() => setShowingDue(true)}>
                {t.dueNoticeAction}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  rememberNoticeHidden();
                  setHidden(true);
                }}
              >
                {t.hideNotice}
              </Button>
            </span>
          </div>
        )}
        {count > 0 && (hidden || chosen || showingDue) && !showingDue && (
          <div>
            <Button variant="ghost" size="sm" onClick={() => { setChosen(null); setShowingDue(true); }}>
              {t.dueNotice(count)}
            </Button>
          </div>
        )}

        {showingDue ? (
          <section className="flex flex-col gap-2">
            <h2 className="text-title">{t.dueTitle}</h2>
            {count === 0 ? (
              <p className="text-subtitle text-muted-foreground">{t.dueEmpty}</p>
            ) : (
              <ul className="flex flex-col divide-y">
                {due?.map((one) => (
                  <li key={one.id} className="flex items-start gap-3 py-3">
                    <div className="pt-0.5">
                      <MarkedAvatar name={one.character_name ?? nameOf(one.character_id)} src={avatarOf(one.character_id)} size="sm" />
                    </div>
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <p className="text-body break-words">{one.text}</p>
                      <span className="text-subtitle text-muted-foreground">
                        {one.character_name ?? nameOf(one.character_id)} · {t.daysLeft(one.days_left ?? 0)}
                      </span>
                    </div>
                    <span className="flex shrink-0 items-center gap-1">
                      <Button variant="ghost" size="sm" disabled={busy.has(one.id)} onClick={() => keep(one)}>
                        {t.keep30}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={busy.has(one.id)}
                        onClick={() => {
                          setPendingDelete(one);
                          setConfirmingDelete(true);
                        }}
                      >
                        {t.remove}
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : !chosen ? (
          <section className="flex flex-col gap-3">
            <h2 className="text-title">{t.pickCharacter}</h2>
            {characters.length === 0 ? (
              <Spinner />
            ) : (
              <ul className="grid grid-cols-4 gap-3 sm:grid-cols-6">
                {characters.map((character) => (
                  <li key={character.id}>
                    <button
                      type="button"
                      className="flex w-full flex-col items-center gap-1 rounded-md py-2 hover:bg-accent"
                      onClick={() => setChosen(character.id)}
                    >
                      <MarkedAvatar name={character.name} src={character.avatar_url} size="lg" />
                      <span className="text-subtitle">{character.name}</span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        ) : (
          <section className="flex flex-col gap-2">
            <h2 className="text-title">{nameOf(chosen)}</h2>
            {hers === null ? (
              <Spinner />
            ) : hers.length === 0 ? (
              <Empty>
                <EmptyHeader>
                  <EmptyTitle>{t.empty}</EmptyTitle>
                </EmptyHeader>
              </Empty>
            ) : (
              <ul className="flex flex-col divide-y">
                {hers.map((one) => (
                  <li key={one.id} className="flex items-start gap-3 py-4">
                    <div className="flex min-w-0 flex-1 flex-col gap-1">
                      <button
                        type="button"
                        className="text-left"
                        disabled={busy.has(one.id) || one.pinned}
                        onClick={() => keep(one)}
                      >
                        <span className="text-body break-words">{one.text}</span>
                      </button>
                      {one.summary && (
                        <span className="text-subtitle break-words text-muted-foreground">{one.summary}</span>
                      )}
                      <span className="flex flex-wrap items-center gap-2 text-subtitle text-muted-foreground">
                        {one.due_soon && one.days_left !== null && (
                          <span className="size-2 shrink-0 rounded-full bg-destructive" aria-hidden />
                        )}
                        {one.pinned ? (
                          <Badge variant="secondary">{t.pinned}</Badge>
                        ) : one.days_left !== null ? (
                          one.due_soon ? t.dueLeftHint(one.days_left) : t.daysLeft(one.days_left)
                        ) : null}
                        {one.url && (
                          <a
                            className="inline-flex items-center gap-1 underline"
                            href={one.url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {t.source}
                            <ExternalLinkIcon className="size-3" aria-hidden />
                          </a>
                        )}
                      </span>
                    </div>
                    <span className="flex shrink-0 items-center gap-1">
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={one.pinned ? t.unkeep : t.keep}
                        disabled={busy.has(one.id)}
                        onClick={() => pin(one, !one.pinned)}
                      >
                        {busy.has(one.id) ? <Spinner /> : one.pinned ? <UnpinIcon /> : <PinIcon />}
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={t.removeLabel(one.text)}
                        disabled={busy.has(one.id)}
                        onClick={() => {
                          setPendingDelete(one);
                          setConfirmingDelete(true);
                        }}
                      >
                        <TrashIcon />
                      </Button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>

      <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t.removeTitle}</AlertDialogTitle>
            <AlertDialogDescription>
              {pendingDelete ? t.removeDescription(pendingDelete.text) : ""}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{copy.common.cancel}</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => pendingDelete && remove(pendingDelete)}
            >
              {t.remove}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </SettingsPane>
  );
}

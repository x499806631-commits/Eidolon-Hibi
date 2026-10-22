"use client";

import { useEffect, useMemo, useState } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { PageShell } from "@/components/app/PageShell";
import { useDirectory, useWide } from "@/components/conversations/ConversationDirectory";
import { Button } from "@/components/ui/button";
import { Empty, EmptyDescription, EmptyHeader, EmptyMedia, EmptyTitle } from "@/components/ui/empty";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { copy } from "@/i18n";
import type { Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import {
  fillImages,
  getCabinet,
  giversIn,
  ofIdol,
  seeKeepsakes,
  unseenKeepsakes,
  type Cabinet,
  type Keepsake,
} from "@/lib/keepsakes";
import { FillImagesIcon, KeepsakesIcon } from "@/theme/icons";

import { KeepsakeDetail, KeepsakeTile } from "./KeepsakeParts";

const t = copy.keepsakes;
const ALL = "all";
/** 补图时多久再读一次柜子，看画好了几张（毫秒）。 */
const FILL_POLL_MS = 5000;

/**
 * 纪念品柜（`/keepsakes`，GLOSSARY.md「纪念品」）：导航外壳里一栏，内容最宽 `max-w-3xl`、居中，同日记页。
 * 顶上一个下拉框按偶像筛选（「全部偶像」和送过东西的每一位：小头像加名字）；下面一格一格的纪念品，
 * 手机三列、电脑四列：图（没有图是她壁纸色上的 emoji）和名字，没看过的右上角一个小点。点一格弹出详情。
 * 有缺图的：配了生图时一个「补上图片（N 张）」的按钮，点了以后每 5 秒再读一次，直到画完；没配时一句说明。
 * 打开柜子就算都看过了。
 */
export function KeepsakesPage() {
  const [cabinet, setCabinet] = useState<Cabinet | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [idol, setIdol] = useState<string>(ALL);
  const [open, setOpen] = useState<Keepsake | null>(null);
  const { characters, go } = useDirectory();
  const isWide = useWide();
  const byId = useMemo(() => new Map((characters ?? []).map((c) => [c.id, c])), [characters]);

  useEffect(() => {
    let current = true;
    getCabinet().then(
      (c) => {
        if (!current) return;
        setCabinet(c);
        if (c.keepsakes.some((k) => !k.seen)) {
          seeKeepsakes().then(() => unseenKeepsakes.clear(), () => {});
        }
      },
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, []);

  // 补图中：隔一会儿再读，画好的就换上图。
  const filling = cabinet?.filling ?? false;
  useEffect(() => {
    if (!filling) return;
    const timer = setInterval(() => {
      getCabinet().then(setCabinet, () => {});
    }, FILL_POLL_MS);
    return () => clearInterval(timer);
  }, [filling]);

  const fill = () => {
    fillImages().then(
      (c) => setCabinet((before) => before && { ...before, ...c }),
      (e) => setFailure(toFailure(e)),
    );
  };

  const order = (characters ?? []).map((c) => c.id);
  const givers = cabinet ? giversIn(cabinet.keepsakes, order) : [];
  const shown = cabinet ? ofIdol(cabinet.keepsakes, idol === ALL ? null : idol) : [];
  const chat = (her: Character) => go(`/conversation/${encodeURIComponent(her.id)}`);

  return (
    <PageShell width="full" isInShell header={<h1 className="text-list-title">{t.title}</h1>}>
      <div className="mx-auto flex w-full max-w-3xl flex-1 flex-col gap-4 px-4 py-6">
        {failure && <FailureNotice failure={failure} />}
        {cabinet === null || characters === null ? (
          !failure && <p className="text-body text-muted-foreground">{t.loading}</p>
        ) : cabinet.keepsakes.length === 0 ? (
          <Empty>
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <KeepsakesIcon />
              </EmptyMedia>
              <EmptyTitle>{t.empty}</EmptyTitle>
              <EmptyDescription>{t.emptyHint}</EmptyDescription>
            </EmptyHeader>
          </Empty>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <IdolFilter value={idol} onChange={setIdol} givers={givers} byId={byId} />
              {cabinet.missing > 0 && cabinet.can_draw && (
                <Button variant="outline" size="sm" disabled={cabinet.filling} onClick={fill}>
                  <FillImagesIcon />
                  {cabinet.filling ? t.filling : t.fill(cabinet.missing)}
                </Button>
              )}
            </div>
            {cabinet.missing > 0 && (
              <p className="text-subtitle text-muted-foreground">
                {cabinet.can_draw ? t.fillHint : t.cannotDraw(cabinet.missing)}
              </p>
            )}
            <ul className="grid grid-cols-3 gap-2 md:grid-cols-4">
              {shown.map((keepsake) => {
                const her = byId.get(keepsake.character_id);
                return her ? (
                  <KeepsakeTile key={keepsake.id} keepsake={keepsake} her={her} onOpen={() => setOpen(keepsake)} />
                ) : null;
              })}
            </ul>
          </>
        )}
      </div>
      <KeepsakeDetail
        keepsake={open}
        her={open ? byId.get(open.character_id) : undefined}
        isWide={isWide}
        onClose={() => setOpen(null)}
        onChat={chat}
      />
    </PageShell>
  );
}

/** 按偶像筛选：「全部偶像」和送过东西的每一位，小头像加名字。 */
function IdolFilter({
  value,
  onChange,
  givers,
  byId,
}: {
  value: string;
  onChange: (value: string) => void;
  givers: string[];
  byId: Map<string, Character>;
}) {
  const items = [{ value: ALL, label: t.all }, ...givers.map((id) => ({ value: id, label: byId.get(id)?.name ?? id }))];
  const show = (id: string) => {
    const her = byId.get(id);
    if (id === ALL || !her) return <span>{t.all}</span>;
    return (
      <span className="flex items-center gap-2">
        <MarkedAvatar name={her.name} src={her.avatar_url} size="sm" />
        {her.name}
      </span>
    );
  };
  return (
    <Select items={items} value={value} onValueChange={(next) => onChange(typeof next === "string" ? next : ALL)}>
      <SelectTrigger aria-label={t.filterLabel} className="h-9 min-w-44">
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

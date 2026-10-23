"use client";

import { ChevronUpIcon } from "lucide-react";
import NextLink from "next/link";
import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";

import { FailureNotice } from "@/components/app/FailureNotice";
import { MarkedAvatar } from "@/components/app/MarkedAvatar";
import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { SettingsPane } from "@/components/settings/SettingsPane";
import { Heatmap } from "@/components/stats/Heatmap";
import { HourChart } from "@/components/stats/HourChart";
import { ShareButton } from "@/components/stats/ShareButton";
import { Button } from "@/components/ui/button";
import { Empty, EmptyContent, EmptyHeader, EmptyTitle } from "@/components/ui/empty";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { copy } from "@/i18n";
import { getStats, type Character } from "@/lib/api";
import { toFailure, type Failure } from "@/lib/failure";
import { FIRST_LEVEL } from "@/lib/navShell";
import { canShare } from "@/lib/shareCard";
import {
  affinityGroups,
  callTime,
  callTimeText,
  mostCalled,
  mostTalked,
  portrait,
  STATS_RANGES,
  statsSections,
  tierPreview,
  type Ranked,
  type Stats,
  type StatsRange,
  type TierGroup,
} from "@/lib/stats";
import { cn } from "@/lib/utils";
import { herVars } from "@/theme/herVars";

const t = copy.settings.stats;

const CONVERSATIONS = FIRST_LEVEL.conversations;

/**
 * 设置「统计」（GLOSSARY.md「统计」，DESIGN.md「统计页」）。上半部分不随时间跨度变，从上到下是一句话画像、
 * 认识天数和连续来访三个数、过去一年的热力图、好感度分组和「成就」两个数（解锁的阶段、集齐的角色）；下半部分顶上是「近 30 天 / 近一年 / 全部」，默认全部，下面是总数、聊得最多、
 * 通话最久、分时段图和礼物。好感度和礼物关掉时，好感度分组、成就、礼物和总数里的「送出的礼物」都不显示。哪一块没数据就不显示；一个回合、一次送礼、一通通话都没有时整页只有一句话和去对话列表的按钮。
 * 每次打开都重新读，删掉历史对话后回来就是新的数；换跨度时重新读整份，新的到之前眼前的数留着、变淡，页面不跳不空。
 */
export function StatsSettings() {
  const [range, setRange] = useState<StatsRange>("all");
  const [stats, setStats] = useState<Stats | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [shareFailure, setShareFailure] = useState<Failure | null>(null);
  const { guard, characters, failure: directoryFailure } = useDirectory();

  useEffect(() => {
    let current = true;
    getStats(range).then(
      (got) => {
        if (!current) return;
        setStats(got);
        setFailure(null);
      },
      (e) => current && setFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [range]);

  // 排行和礼物要角色的名字和颜色：角色还没到时整页当作读取中，免得那几行晚一步冒出来、页面跳一下。
  if (!stats || characters === null) {
    const shownFailure = stats ? directoryFailure : failure;
    return (
      <SettingsPane category="stats">
        {shownFailure ? (
          <FailureNotice failure={shownFailure} />
        ) : (
          <p className="text-body text-muted-foreground">{copy.settings.loading}</p>
        )}
      </SettingsPane>
    );
  }

  const shown = statsSections(stats);
  if (shown.isEmpty) {
    return (
      <SettingsPane category="stats">
        <Empty>
          <EmptyHeader>
            <EmptyTitle>{t.empty}</EmptyTitle>
          </EmptyHeader>
          <EmptyContent>
            <Button
              variant="outline"
              nativeButton={false}
              render={<NextLink href={CONVERSATIONS} onClick={guard(CONVERSATIONS)} />}
            >
              {t.toConversations}
            </Button>
          </EmptyContent>
        </Empty>
      </SettingsPane>
    );
  }

  const byId = new Map(characters.map((c) => [c.id, c]));
  /** 眼前的下半部分是上一个跨度的：换了以后新的还没到，或者没取到。 */
  const otherRange = stats.range.span !== range;
  const { calls, gifts } = stats.range;
  const giftee = gifts?.most && byId.get(gifts.most.character_id);

  return (
    <SettingsPane
      category="stats"
      end={
        canShare(stats) && <ShareButton stats={stats} byId={byId} disabled={otherRange} onFailure={setShareFailure} />
      }
    >
      <div className="flex flex-col gap-8">
        {shareFailure && <FailureNotice failure={shareFailure} />}
        <div className="flex flex-col gap-4">
          {shown.portrait && <p className="text-header">{portrait(stats.all_hours)}</p>}
          <dl className="grid grid-cols-3 gap-3">
            <Figure label={t.daysKnown} value={stats.days_known} unit={t.days} />
            {shown.visits && (
              <>
                <Figure label={t.currentVisits} value={stats.visits.current} unit={t.days} />
                <Figure label={t.longestVisits} value={stats.visits.longest} unit={t.days} />
              </>
            )}
          </dl>
        </div>
        {shown.heatmap && (
          <section className="flex flex-col gap-2">
            <h2 className="text-header">{t.heatmap}</h2>
            <Heatmap heatmap={stats.heatmap} />
          </section>
        )}
        {shown.affinity && <AffinityGroups groups={affinityGroups(stats.affinity)} byId={byId} />}
        {shown.achievements && stats.achievements && (
          <section className="flex flex-col gap-3">
            <h2 className="text-header">{t.achievements}</h2>
            <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              <Figure label={t.stagesUnlocked} value={stats.achievements.stages} unit={t.stagesUnit} />
              <Figure label={t.charactersComplete} value={stats.achievements.complete} unit={t.charactersUnit} />
            </dl>
          </section>
        )}
        <div className="flex flex-col gap-4">
          <Tabs
            value={range}
            onValueChange={(value) => {
              setRange(value as StatsRange);
              setShareFailure(null);
            }}
          >
            <TabsList aria-label={t.rangeLabel}>
              {STATS_RANGES.map((r) => (
                <TabsTrigger key={r} value={r}>
                  {t.ranges[r]}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          {failure && <FailureNotice failure={failure} />}
          <div
            aria-busy={otherRange}
            className={cn("flex flex-col gap-8 transition-opacity duration-200", otherRange && "opacity-45")}
          >
            {shown.totals && (
              <dl className={cn("grid grid-cols-2 gap-3", shown.giftsGiven ? "sm:grid-cols-4" : "sm:grid-cols-3")}>
                <Figure label={t.turns} value={stats.range.turns} />
                <Figure label={t.calls} value={calls.count} unit={t.timesUnit} />
                <FigureOf label={t.callTime}>
                  <CallTime seconds={calls.seconds} />
                </FigureOf>
                {shown.giftsGiven && gifts && <Figure label={t.giftsGiven} value={gifts.given} />}
              </dl>
            )}
            {shown.mostTalked && (
              <Ranking
                title={t.mostTalked}
                ranked={mostTalked(stats.range.characters)}
                byId={byId}
                reading={(ranked) => t.talked(ranked.turns, ranked.days_known)}
              />
            )}
            {shown.mostCalled && (
              <Ranking
                title={t.mostCalled}
                ranked={mostCalled(stats.range.characters)}
                byId={byId}
                reading={(ranked) => callTimeText(ranked.call_seconds)}
              />
            )}
            {shown.hours && (
              <section className="flex flex-col gap-2">
                <h2 className="text-header">{t.hours}</h2>
                <HourChart hours={stats.range.by_hour} />
              </section>
            )}
            {shown.gifts && gifts && (
              <section className="flex flex-col gap-3">
                <h2 className="text-header">{t.gifts}</h2>
                <dl className="grid grid-cols-2 gap-3 sm:grid-cols-3">
                  {gifts.most && giftee && (
                    <FigureOf label={t.mostGiven}>
                      <Giftee character={giftee} given={gifts.most.given} />
                    </FigureOf>
                  )}
                  <Figure label={t.likedGiven} value={gifts.liked} unit={t.timesUnit} />
                </dl>
              </section>
            )}
          </div>
        </div>
      </div>
    </SettingsPane>
  );
}

/** 一个数：上面 Subtitle 字号 muted 的标签，下面 1.5rem 的数，后面跟单位。 */
function Figure({ label, value, unit }: { label: string; value: number; unit?: string }) {
  return (
    <FigureOf label={label}>
      <Amount value={value} unit={unit} />
    </FigureOf>
  );
}

function Amount({ value, unit }: { value: number; unit?: string }) {
  return (
    <>
      <span className="text-2xl font-semibold tabular-nums">{value}</span>
      {unit && <span className="text-subtitle text-muted-foreground">{unit}</span>}
    </>
  );
}

/** 通话时长：「2 小时 5 分」，不满 1 小时只写「12 分」。 */
function CallTime({ seconds }: { seconds: number }) {
  const { hours, minutes } = callTime(seconds);
  return (
    <>
      {hours > 0 && <Amount value={hours} unit={t.hoursUnit} />}
      <Amount value={minutes} unit={t.minutesUnit} />
    </>
  );
}

/** 一个排行：Header 字号的标题，下面每行她的头像、名字、行尾的读数，和一根她的 `--her-bar` 的条，长短按占第一位的比例。 */
function Ranking({
  title,
  ranked,
  byId,
  reading,
}: {
  title: string;
  ranked: Ranked[];
  byId: Map<string, Character>;
  reading: (ranked: Ranked) => string;
}) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-header">{title}</h2>
      <ol className="flex flex-col gap-3">
        {ranked.map((one) => {
          const character = byId.get(one.character_id);
          if (!character) return null;
          return (
            <li key={one.character_id} style={herVars(character.color)} className="flex items-center gap-3">
              <MarkedAvatar name={character.name} src={character.avatar_url} size="sm" />
              <div className="flex min-w-0 flex-1 flex-col gap-1.5">
                <div className="flex items-baseline justify-between gap-2">
                  <span className="truncate text-sm font-medium">{character.name}</span>
                  <span className="shrink-0 text-subtitle text-muted-foreground tabular-nums">{reading(one)}</span>
                </div>
                <div className="h-2 rounded-full bg-muted">
                  <div className="h-full rounded-full bg-her-bar" style={{ width: `${one.share * 100}%` }} />
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

/**
 * 好感度分组：Header 字号的标题，下面每档一行，左边 Subtitle 字号 muted 的档名，右边这一档里她们的头像，外面一圈她的 `--her-mark`。
 * 一档里人多时收起来只露前几位（`lib/stats` 的 `tierPreview`），后面一个「+N」：电脑上鼠标移到这一行时展开，移开收回；
 * 点「+N」（手机上只能点）展开到点末尾的收起键为止。
 */
function AffinityGroups({ groups, byId }: { groups: TierGroup[]; byId: Map<string, Character> }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-header">{t.affinity}</h2>
      <dl className="grid grid-cols-[auto_1fr] items-start gap-x-4 gap-y-3">
        {groups.map((group) => (
          <Fragment key={group.tier}>
            {/* 档名对着第一排头像的中线，展开时不跟着跳到中间。 */}
            <dt className="flex h-9 items-center text-subtitle text-muted-foreground">
              {copy.conversation.affinity.tiers[group.tier]}
            </dt>
            <dd>
              <TierMembers characters={group.characters} byId={byId} />
            </dd>
          </Fragment>
        ))}
      </dl>
    </section>
  );
}

/** 收起的她们展开时一个接一个淡入：每位晚 15ms，最多晚 12 位那么久。 */
const STAGGER_MS = 15;
const STAGGER_MOST = 12;

/**
 * 一档里的她们：收起时前几位加「+N」，鼠标在这一行上或点开后是全部。展开、收回时这一行的高度 200ms 过渡（量出列表的高度给外层），
 * 收起的她们淡入放大、一个接一个出来，「+N」回来时淡入；开了减少动态效果时都不动。外层多出 4px 给头像外的描边，免得被裁掉。
 */
function TierMembers({ characters, byId }: { characters: string[]; byId: Map<string, Character> }) {
  const [isOpen, setIsOpen] = useState(false);
  const [height, setHeight] = useState<number | null>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const shown = tierPreview(characters.length);
  const folded = characters.length - shown;

  useEffect(() => {
    const list = listRef.current;
    if (!list) return;
    const observer = new ResizeObserver(() => setHeight(list.offsetHeight));
    observer.observe(list);
    return () => observer.disconnect();
  }, []);

  return (
    <div
      style={height === null ? undefined : { height }}
      className="group/tier -m-1 overflow-hidden transition-[height] duration-200 ease-out motion-reduce:transition-none"
    >
      <ul ref={listRef} className="flex flex-wrap gap-2.5 p-1">
        {characters.map((id, i) => {
          const character = byId.get(id);
          if (!character) return null;
          const isFolded = i >= shown;
          return (
            <li
              key={id}
              style={{
                ...herVars(character.color),
                ...(isFolded && { animationDelay: `${Math.min(i - shown, STAGGER_MOST) * STAGGER_MS}ms` }),
              }}
              className={cn(
                "rounded-full ring-2 ring-her-mark ring-offset-2 ring-offset-background",
                isFolded && "animate-in duration-200 fade-in zoom-in-75 fill-mode-backwards motion-reduce:animate-none",
                isFolded && !isOpen && "hidden group-hover/tier:block",
              )}
            >
              <MarkedAvatar name={character.name} src={character.avatar_url} size="bubble" />
            </li>
          );
        })}
        {folded > 0 && (
          <li
            className={cn(
              "animate-in duration-200 fade-in motion-reduce:animate-none",
              !isOpen && "group-hover/tier:hidden",
            )}
          >
            <Button
              variant="more"
              size="icon-lg"
              aria-expanded={isOpen}
              aria-label={isOpen ? t.tierLess : t.tierMoreLabel(folded)}
              onClick={() => setIsOpen(!isOpen)}
            >
              {isOpen ? <ChevronUpIcon /> : t.tierMore(folded)}
            </Button>
          </li>
        )}
      </ul>
    </div>
  );
}

function FigureOf({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="text-subtitle text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 items-baseline gap-1">{children}</dd>
    </div>
  );
}

/** 送得最多的那位：头像、名字和送了几次。 */
function Giftee({ character, given }: { character: Character; given: number }) {
  return (
    <span className="flex min-w-0 items-center gap-2 self-center">
      <MarkedAvatar name={character.name} src={character.avatar_url} size="sm" />
      <span className="truncate text-sm font-medium">{character.name}</span>
      <span className="shrink-0 text-subtitle text-muted-foreground tabular-nums">{t.times(given)}</span>
    </span>
  );
}

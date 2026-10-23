"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { flushSync } from "react-dom";

import { AppLink } from "@/components/app/AppLink";
import { AdminPage } from "@/components/app/admin/AdminPage";
import { StatTile } from "@/components/app/admin/StatTile";
import { StatusDot } from "@/components/app/StatusDot";
import { BackupCard } from "@/components/admin/BackupCard";
import { CostSection } from "@/components/admin/CostSection";
import {
  BAR_BODY,
  BAR_ROW,
  CardPair,
  DashboardCard,
  DashboardGroup,
  Folded,
  RowList,
  ShareBar,
  TableOrRows,
  TileGroup,
} from "@/components/admin/DashboardParts";
import { TrendChart } from "@/components/admin/TrendChart";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Spinner } from "@/components/ui/spinner";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useWhileVisible } from "@/hooks/useWhileVisible";
import { copy } from "@/i18n";
import {
  AFFINITY_LEVELS,
  type AffinityRow,
  type AffinitySection,
  affinityRows,
  CALL_END_REASONS,
  CALL_RESULTS,
  achievementTiles,
  countTicks,
  DASHBOARD_RANGES,
  DASHBOARD_REFRESH_MS,
  dailyTaskTiles,
  type Dashboard,
  dashboardHref,
  dashboardTabs,
  type DashboardRange,
  type DashboardTab,
  durationRange,
  type ErrorKind,
  formatCacheHitRate,
  formatCount,
  formatDuration,
  formatFetchedAt,
  formatMs,
  formatPercent,
  formatPercentiles,
  formatShare,
  formatUsage,
  getCallsInProgress,
  getDashboard,
  type HealthStatus,
  healthRowId,
  healthRowName,
  healthStatus,
  healthSummary,
  isDashboardRange,
  isDashboardTab,
  reachOutTiles,
  serviceName,
  splitZeroRows,
  strippedText,
  TURN_RESULTS,
} from "@/lib/dashboard";
import { cn } from "@/lib/utils";
import { FailureIcon } from "@/theme/icons";

const t = copy.admin.dashboard;

/** 页首健康摘要的锚点：链到「概览」里「服务健康」那张卡。 */
const HEALTH_ANCHOR = "health";
/** 摘要里最多点名几项有问题的服务，再多写「等 N 项」。 */
const SUMMARY_NAMES = 3;

/** 出问题的两档对应的状态点；正常、没有调用不加点（DESIGN.md「Admin status & charts」）。 */
const STATUS_DOT: Record<"degraded" | "failing", "warning" | "error"> = {
  degraded: "warning",
  failing: "error",
};

/** 新数字到之前，旧的变淡：一眼看得出眼前的数字不属于刚选的范围。 */
const FADE = "transition-opacity duration-200";
const STALE = "opacity-45";

/**
 * 看板（后台默认页）：时间范围切换；页首下面一排分组标签，一次只看一组——「概览」（回合和通话的几个数字、
 * 进行中的通话数、服务健康、备份）、「用量」（趋势、各种结果、通话时长、按角色、搭话）、「好感度」（好感度生效时才有）、
 * 「花费」。数字全由后端汇总，这里只负责排版和格式化。
 *
 * 数字要看得出新旧：页首下面写「数据截至 16:25」，旁边可以手动刷新；页面看得见时每分钟自动重新取一次
 * （切到别的标签页就停，切回来时过了一分钟就马上取），自动刷新不闪、不变淡。切换时间范围或手动刷新时，
 * 新数字到之前旧的整体变淡并标 aria-busy，不让人把上一个范围的数字当成这个范围的。取不到时旧数字留着，
 * 提示写明下面是几点的数据。时间范围和分组记在地址里（`?range=7d&tab=cost`），可以收藏、刷新不丢。
 * 切走的分组不卸，展开过的长列表切回来还开着。
 */
export function AdminDashboard({ initialRange, initialTab }: { initialRange: DashboardRange; initialTab: DashboardTab }) {
  const [range, setRange] = useState<DashboardRange>(initialRange);
  const [tab, setTab] = useState<DashboardTab>(initialTab);
  const [board, setBoard] = useState<Dashboard | null>(null);
  const [fetchedAt, setFetchedAt] = useState<Date | null>(null);
  /** 切换范围或手动刷新、新数字还没到：旧的变淡。自动刷新不算。 */
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [inProgress, setInProgress] = useState<number | null>(null);
  /** 页首的「刷新」点了几次：「备份」那块自己轮询，点刷新时跟着再问一次。 */
  const [refreshes, setRefreshes] = useState(0);
  const rangeRef = useRef(range);
  /** 只认最后发出的那次请求：先发的自动刷新晚到，不能盖掉刚切换的范围。 */
  const latest = useRef(0);

  const load = useCallback((which: DashboardRange, quietly: boolean) => {
    const request = ++latest.current;
    if (!quietly) setLoading(true);
    getDashboard(which).then(
      (loaded) => {
        if (request !== latest.current) return;
        setBoard(loaded);
        setFetchedAt(new Date());
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        if (request !== latest.current) return;
        setError(e.message);
        setLoading(false);
      },
    );
  }, []);

  const refreshCalls = useCallback(() => {
    getCallsInProgress().then(setInProgress, () => setInProgress(null));
  }, []);

  useEffect(() => {
    rangeRef.current = range;
    load(range, false);
  }, [range, load]);

  useEffect(refreshCalls, [refreshCalls]);

  useWhileVisible(() => {
    load(rangeRef.current, true);
    refreshCalls();
  }, DASHBOARD_REFRESH_MS);

  function chooseRange(value: string) {
    if (!isDashboardRange(value)) return;
    setRange(value);
    window.history.replaceState(null, "", dashboardHref(window.location.pathname, value, tab));
  }

  function chooseTab(value: string) {
    if (!isDashboardTab(value)) return;
    setTab(value);
    window.history.replaceState(null, "", dashboardHref(window.location.pathname, range, value));
  }

  /** 点了页首的「看服务健康」：先同步切回「概览」，让那张卡露出来，链接再照常滚过去。 */
  function showHealth() {
    if (tab !== "overview") flushSync(() => chooseTab("overview"));
  }

  function refresh() {
    load(range, false);
    refreshCalls();
    setRefreshes((n) => n + 1);
  }

  const stamp = fetchedAt && formatFetchedAt(fetchedAt);
  /** 眼前的数字是上一个范围的：切换后还没到，或者切换那次没取到。 */
  const otherRange = board !== null && board.range !== range;
  const faded = loading || otherRange;
  return (
    <AdminPage
      width="wide"
      title={copy.admin.pages.dashboard}
      intro={t.intro}
      end={
        <Tabs value={range} onValueChange={(value) => chooseRange(String(value))}>
          <TabsList aria-label={t.rangeLabel}>
            {DASHBOARD_RANGES.map((r) => (
              <TabsTrigger key={r} value={r}>
                {t.ranges[r]}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      }
    >
      {/* 页首下面一行：健康结论和「数据截至」。手机上折成两行。 */}
      <div className="-mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        {/* 健康结论也是上一个范围的，和下面的数字一起变淡。 */}
        {board && (
          <div className={cn(FADE, faded && STALE)}>
            <HealthSummary board={board} onShow={showHealth} />
          </div>
        )}
        <Freshness stamp={stamp} loading={loading} onRefresh={refresh} />
      </div>
      {error !== null && (
        <Alert>
          <FailureIcon />
          <AlertTitle>{t.failed(error)}</AlertTitle>
          {board && stamp && (
            <AlertDescription>
              {otherRange ? t.freshness.shownFromRange(stamp, t.ranges[board.range]) : t.freshness.shownFrom(stamp)}
            </AlertDescription>
          )}
        </Alert>
      )}
      {board === null ? (
        error === null && <Skeleton className="h-80" />
      ) : (
        <DashboardBody
          board={board}
          tab={tab}
          onTab={chooseTab}
          inProgress={inProgress}
          loading={loading}
          faded={faded}
          refreshes={refreshes}
        />
      )}
    </AdminPage>
  );
}

/** 「数据截至 16:25」和刷新键。第一次读取时写「正在读取…」，占住这一行，数字到了不跳。 */
function Freshness({ stamp, loading, onRefresh }: { stamp: string | null; loading: boolean; onRefresh: () => void }) {
  return (
    <div className="flex items-center gap-1 text-muted-foreground">
      <span className="tabular-nums">{stamp ? t.freshness.at(stamp) : t.freshness.loading}</span>
      {stamp && (
        // 手机上撑到 44px 的触控区。读取中不让再点，但焦点留在键上。
        <Button
          variant="ghost"
          size="sm"
          aria-label={t.freshness.refreshLabel}
          disabled={loading}
          focusableWhenDisabled
          onClick={onRefresh}
          className="max-md:min-h-11"
        >
          {loading && <Spinner data-icon="inline-start" />}
          {t.freshness.refresh}
        </Button>
      )}
    </div>
  );
}

/**
 * 页首下面的一句健康结论：都正常、没有调用（只写 muted 字），或者点名出问题的服务（最糟的在前，带琥珀或红点），
 * 链到「概览」里「服务健康」那张卡（在别的分组时先切回「概览」）。只占一行。
 */
function HealthSummary({ board, onShow }: { board: Dashboard; onShow: () => void }) {
  const summary = healthSummary(board.health);
  const words = t.health.summary;
  let text: string;
  if (summary.kind === "problems") {
    const named = summary.rows.slice(0, SUMMARY_NAMES).map((row) =>
      words.problem(
        healthRowName(row),
        t.health.statuses[healthStatus(row)],
        formatPercent(row.error_rate),
      ),
    );
    const more = summary.rows.length > SUMMARY_NAMES ? words.more(formatCount(summary.rows.length)) : "";
    text = named.join("、") + more;
  } else {
    text = words[summary.kind];
  }
  return (
    <p className="flex flex-wrap items-center gap-x-3">
      {summary.kind === "problems" ? (
        <span className="inline-flex items-center gap-2 font-medium">
          <StatusDot variant={STATUS_DOT[summary.worst]} />
          {text}
        </span>
      ) : (
        <span className="text-muted-foreground">{text}</span>
      )}
      <AppLink href={`#${HEALTH_ANCHOR}`} onClick={onShow} className="inline-flex items-center max-md:min-h-11">
        {words.link}
      </AppLink>
    </p>
  );
}

/**
 * 分组标签（下划线样式，和页首胶囊样式的时间范围分开）和各组的内容：
 * - 「概览」：两组数字卡（「此刻」一张，「{范围}内」五张，手机上只留回合、通话、活跃用户三张）、服务健康、备份。
 * - 「用量」：趋势、结果、通话时长、结束原因各一张卡，宽屏两张一排；下面是按角色，再下面「搭话」三张数字卡（为空时没有）。
 * - 「好感度」：好感度生效时才有这个标签；地址里要了它而好感度没开时，显示「概览」。
 * - 「花费」。
 */
function DashboardBody({
  board,
  tab,
  onTab,
  inProgress,
  loading,
  faded,
  refreshes,
}: {
  board: Dashboard;
  tab: DashboardTab;
  onTab: (value: string) => void;
  inProgress: number | null;
  loading: boolean;
  /** 眼前是上一个范围的数字，或者新数字还在路上：按范围统计的部分变淡，「此刻」那张和备份不变。 */
  faded: boolean;
  refreshes: number;
}) {
  const { turns, calls } = board;
  const fade = cn(FADE, faded && STALE);
  const tabs = dashboardTabs(board);
  const shown = tabs.includes(tab) ? tab : "overview";
  return (
    <Tabs value={shown} onValueChange={(value) => onTab(String(value))} aria-busy={loading} className="gap-6">
      <TabsList variant="line" aria-label={t.tabsLabel}>
        {tabs.map((name) => (
          <TabsTrigger key={name} value={name}>
            {t.tabs[name]}
          </TabsTrigger>
        ))}
      </TabsList>

      <TabsContent value="overview" keepMounted className="flex flex-col gap-6">
        {/* 「进行中的通话」是此刻的数，不随时间范围变：单独一组放在前面，和按范围统计的几张隔开。
            宽屏（≥ 1280px）左右排，此刻一张占一份、范围五张占五份；更窄时上下排，此刻那张仍按同样的格子只占一格。 */}
        <div className="grid gap-6 xl:grid-cols-[1fr_5fr]">
          <TileGroup caption={t.tiles.now} grid="grid-cols-2 md:grid-cols-3 lg:grid-cols-5 xl:grid-cols-1">
            <StatTile
              label={t.tiles.callsInProgress}
              value={inProgress === null ? "—" : formatCount(inProgress)}
            />
          </TileGroup>
          <TileGroup caption={t.tiles.inRange(t.ranges[board.range])} className={fade}>
            <StatTile
              label={t.tiles.turns}
              value={formatCount(turns.total)}
              note={t.tiles.turnsInCalls(formatCount(turns.in_calls))}
            />
            <StatTile
              label={t.tiles.calls}
              value={formatCount(calls.total)}
              note={t.tiles.callsExempt(formatCount(calls.own_keys))}
            />
            <StatTile label={t.tiles.activeUsers} value={formatCount(board.active_users)} />
            {/* 这两张手机上不显示。外面包一层 grid，卡片仍撑满格子的高。 */}
            <div className="grid max-md:hidden">
              <StatTile
                label={t.tiles.ownKeys}
                value={formatCount(turns.own_keys)}
                note={t.tiles.ownKeysShare(formatShare(turns.own_keys, turns.total))}
              />
            </div>
            <div className="grid max-md:hidden">
              <StatTile
                label={t.tiles.timeToSpeak}
                value={formatMs(turns.time_to_speak_ms.p50)}
                note={
                  turns.time_to_speak_ms.p90 === null ? undefined : t.tiles.timeToSpeakP90(formatMs(turns.time_to_speak_ms.p90))
                }
              />
            </div>
          </TileGroup>
        </div>

        <DashboardCard id={HEALTH_ANCHOR} title={t.health.title} level={2} className={cn("scroll-mt-16", fade)}>
          <HealthTable board={board} />
        </DashboardCard>

        {/* 备份是此刻的状态，不随时间范围变，也不变淡。 */}
        <BackupCard refreshKey={refreshes} />
      </TabsContent>

      <TabsContent value="usage" keepMounted className={fade}>
        <DashboardGroup title={t.groups.usage.title}>
          <CardPair>
            <DashboardCard title={t.trend.turns.title} hint={t.trend.turns.hint[board.bucket]}>
              <CountTrend board={board} series="turns" />
            </DashboardCard>
            <DashboardCard title={t.trend.calls.title} hint={t.trend.calls.hint[board.bucket]}>
              <CountTrend board={board} series="calls" />
            </DashboardCard>
            <DashboardCard title={t.results.title}>
              <CountsTable
                rows={TURN_RESULTS.map((result) => ({ id: result, label: t.results.names[result], count: turns.by_result[result] }))}
                total={turns.total}
                labelHeader={t.results.result}
                countHeader={t.results.turns}
                shareHeader={t.results.share}
              />
            </DashboardCard>
            <DashboardCard title={t.callResults.title}>
              <CountsTable
                rows={CALL_RESULTS.map((result) => ({ id: result, label: t.callResults.names[result], count: calls.by_result[result] }))}
                total={calls.total}
                labelHeader={t.callResults.result}
                countHeader={t.callResults.calls}
                shareHeader={t.callResults.share}
              />
            </DashboardCard>
            <DashboardCard
              title={t.durations.title}
              hint={t.durations.hint(formatDuration(calls.duration_s.p50), formatDuration(calls.duration_s.p90))}
            >
              <DurationsTable board={board} />
            </DashboardCard>
            <DashboardCard title={t.endReasons.title}>
              <CountsTable
                rows={CALL_END_REASONS.map((reason) => ({ id: reason, label: t.endReasons.names[reason], count: calls.end_reasons[reason] }))}
                total={CALL_END_REASONS.reduce((sum, reason) => sum + calls.end_reasons[reason], 0)}
                labelHeader={t.endReasons.reason}
                countHeader={t.endReasons.calls}
                shareHeader={t.endReasons.share}
              />
            </DashboardCard>
          </CardPair>
          <DashboardCard title={t.characters.title}>
            <CharactersTable board={board} />
          </DashboardCard>
          <ReachOutTiles board={board} />
        </DashboardGroup>
      </TabsContent>

      {tabs.includes("affinity") && (
        <TabsContent value="affinity" keepMounted className={fade}>
          <DashboardGroup title={t.affinity.title}>
            {board.affinity !== null && <AffinityGroup section={board.affinity} />}
            <TasksAndAchievementsTiles board={board} />
          </DashboardGroup>
        </TabsContent>
      )}

      <TabsContent value="cost" keepMounted className={fade}>
        <CostSection board={board} />
      </TabsContent>
    </Tabs>
  );
}

/** The turns, or the calls, in each stretch, with the users active in it. */
function CountTrend({ board, series }: { board: Dashboard; series: "turns" | "calls" }) {
  const words = t.trend[series];
  return (
    <TrendChart
      points={board.trend.map((p) => ({ start: p.start, value: p[series] }))}
      bucket={board.bucket}
      title={words.title}
      total={words.total(
        t.ranges[board.range],
        formatCount(series === "turns" ? board.turns.total : board.calls.total),
        formatCount(board.active_users),
      )}
      describe={(span, i) =>
        words.tooltip(span, formatCount(board.trend[i][series]), formatCount(board.trend[i].active_users))
      }
      empty={words.empty}
      ticks={countTicks}
      format={formatCount}
    />
  );
}

interface CountRow {
  id: string;
  label: string;
  count: number;
  /** 合并起来的零值行：为 0 的各项名字。 */
  zeroLabels?: string;
}

/** How many of `total` fell under each row's label, and what share of it. The rows at 0
 * fold into one last line, 「其余 N 项为 0」, naming them beneath. */
function CountsTable({
  rows,
  total,
  labelHeader,
  countHeader,
  shareHeader,
}: {
  rows: CountRow[];
  total: number;
  labelHeader: string;
  countHeader: string;
  shareHeader: string;
}) {
  const { counted, zero } = splitZeroRows(rows);
  const shown: CountRow[] =
    zero.length === 0
      ? counted
      : [
          ...counted,
          {
            id: "zero",
            label: (counted.length === 0 ? t.zeroRows.all : t.zeroRows.rest)(formatCount(zero.length)),
            count: 0,
            zeroLabels: zero.map((row) => row.label).join("、"),
          },
        ];
  return (
    <TableOrRows
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{labelHeader}</TableHead>
              <TableHead className="text-right">{countHeader}</TableHead>
              <TableHead className="w-20 text-right">{shareHeader}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((row) =>
              row.zeroLabels === undefined ? (
                <TableRow key={row.id}>
                  <TableCell>{row.label}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatCount(row.count)}</TableCell>
                  <TableCell className="text-right tabular-nums">{formatShare(row.count, total)}</TableCell>
                </TableRow>
              ) : (
                <TableRow key={row.id}>
                  <TableCell colSpan={3} className="whitespace-normal">
                    <span className="text-muted-foreground">{row.label}</span>
                    <span className="block text-xs text-muted-foreground">{row.zeroLabels}</span>
                  </TableCell>
                </TableRow>
              ),
            )}
          </TableBody>
        </Table>
      }
      rows={
        <RowList
          rows={shown.map((row) =>
            row.zeroLabels === undefined
              ? { id: row.id, label: row.label, end: `${formatCount(row.count)} · ${formatShare(row.count, total)}` }
              : { id: row.id, label: <span className="text-muted-foreground">{row.label}</span>, description: row.zeroLabels },
          )}
        />
      }
    />
  );
}

/** How many connected calls lasted how long, a bar against the commonest stretch. */
function DurationsTable({ board }: { board: Dashboard }) {
  const rows = board.calls.durations.map((d) => ({
    id: String(d.from_s),
    label: durationRange(d.from_s, d.to_s),
    calls: d.calls,
  }));
  const most = Math.max(0, ...rows.map((row) => row.calls));
  return (
    <TableOrRows
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t.durations.range}</TableHead>
              <TableHead className="w-1/2" />
              <TableHead className="text-right">{t.durations.calls}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody className={BAR_BODY}>
            {rows.map((row) => (
              <TableRow key={row.id} className={BAR_ROW}>
                <TableCell>{row.label}</TableCell>
                <TableCell>
                  <ShareBar part={row.calls} whole={most} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatCount(row.calls)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      rows={<RowList rows={rows.map((row) => ({ id: row.id, label: row.label, end: formatCount(row.calls) }))} />}
    />
  );
}

function HealthTable({ board }: { board: Dashboard }) {
  if (board.health.length === 0) return <p className="text-sm text-muted-foreground">{t.health.empty}</p>;
  const rows = board.health.map((row) => ({ id: healthRowId(row), row }));
  return (
    <TableOrRows
      at="xl"
      table={
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>{t.health.service}</TableHead>
              <TableHead>{t.health.status}</TableHead>
              <TableHead className="text-right">{t.health.calls}</TableHead>
              <TableHead className="text-right">{t.health.errorRate}</TableHead>
              <TableHead>{t.health.errors}</TableHead>
              <TableHead className="text-right">{t.health.latency}</TableHead>
              <TableHead className="text-right">{t.health.firstToken}</TableHead>
              <TableHead className="text-right">{t.health.cacheHitRate}</TableHead>
              <TableHead>{t.health.usage}</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(({ id, row }) => (
              <TableRow key={id}>
                <TableCell>
                  {serviceName(row)}
                  {row.own_key && <span className="block text-xs text-muted-foreground">{t.health.own}</span>}
                  <StrippedNote row={row} />
                </TableCell>
                <TableCell>
                  <HealthStatusText status={healthStatus(row)} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatCount(row.calls)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPercent(row.error_rate)}</TableCell>
                <TableCell className="whitespace-normal">{errorsText(row)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPercentiles(row.latency_ms)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatPercentiles(row.first_token_ms)}</TableCell>
                <TableCell className="text-right tabular-nums">{formatCacheHitRate(row)}</TableCell>
                <TableCell className="whitespace-normal">{formatUsage(row.usage, row.usage_unknown)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      }
      rows={
        <RowList
          rows={rows.map(({ id, row }) => {
            const errors = errorsText(row);
            const stripped = strippedText(row);
            return {
              id,
              label: healthRowName(row),
              end: <HealthStatusText status={healthStatus(row)} />,
              description: (
                <>
                  <span className="tabular-nums">
                    {`${t.health.calls} ${formatCount(row.calls)} · ${t.health.errorRate} ${formatPercent(row.error_rate)}`}
                  </span>
                  <span className="tabular-nums">
                    {`${t.health.latency} ${formatPercentiles(row.latency_ms)} · ${formatUsage(row.usage, row.usage_unknown)}`}
                  </span>
                  {row.first_token_ms && (
                    <span className="tabular-nums">
                      {`${t.health.firstToken} ${formatPercentiles(row.first_token_ms)} · ${t.health.cacheHitRate} ${formatCacheHitRate(row)}`}
                    </span>
                  )}
                  {errors !== "—" && <span>{`${t.health.errors} ${errors}`}</span>}
                  {stripped && <span>{stripped}</span>}
                </>
              ),
            };
          })}
        />
      }
    />
  );
}

/**
 * 一行服务的状态。正常和没有调用是常态，只写 muted 字、不带点：七行都是绿点只是噪音，页首已经说了
 * 「各项服务正常」；出错偏多（琥珀点）、大量出错（红点）才带点、加粗，一眼找得到。
 */
function HealthStatusText({ status }: { status: HealthStatus }) {
  const label = t.health.statuses[status];
  if (status === "ok" || status === "idle") return <span className="text-muted-foreground">{label}</span>;
  return (
    <span className="inline-flex items-center gap-2 font-medium">
      <StatusDot variant={STATUS_DOT[status]} />
      {label}
    </span>
  );
}

/** 生图一行服务名下面的小字：几张照片剥掉了 tag、各属于黑名单哪一类；没有时不出。 */
function StrippedNote({ row }: { row: Dashboard["health"][number] }) {
  const stripped = strippedText(row);
  return stripped && <span className="block text-xs text-muted-foreground">{stripped}</span>;
}

/** 每类错误各几次，没有时是「—」。 */
function errorsText(row: Dashboard["health"][number]): string {
  return (
    Object.entries(row.errors_by_kind)
      .map(([kind, n]) => `${t.health.errorKinds[kind as ErrorKind] ?? kind} ${formatCount(n ?? 0)}`)
      .join("、") || "—"
  );
}

function CharactersTable({ board }: { board: Dashboard }) {
  if (board.characters.length === 0) return <p className="text-sm text-muted-foreground">{t.characters.empty}</p>;
  const most = Math.max(...board.characters.map((c) => c.turns));
  const name = (c: Dashboard["characters"][number]) =>
    c.name ?? <span className="text-muted-foreground">{t.characters.removed}</span>;
  return (
    <Folded rows={board.characters}>
      {(shown) => (
        <TableOrRows
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.characters.character}</TableHead>
                  <TableHead className="w-2/5" />
                  <TableHead className="text-right">{t.characters.turns}</TableHead>
                  <TableHead className="text-right">{t.characters.calls}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody className={BAR_BODY}>
                {shown.map((c) => (
                  <TableRow key={c.character_id} className={BAR_ROW}>
                    <TableCell>{name(c)}</TableCell>
                    <TableCell>
                      <ShareBar part={c.turns} whole={most} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{formatCount(c.turns)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatCount(c.calls)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
          rows={
            <RowList
              rows={shown.map((c) => ({
                id: c.character_id,
                label: name(c),
                end: `${t.characters.turns} ${formatCount(c.turns)} · ${t.characters.calls} ${formatCount(c.calls)}`,
              }))}
            />
          }
        />
      )}
    </Folded>
  );
}

/** 「用量」里的「搭话」一小节：成功、失败、被回了三张数字卡；为空时（总开关关着、范围内也没有搭话）不显示。 */
function ReachOutTiles({ board }: { board: Dashboard }) {
  const tiles = reachOutTiles(board.reach_outs);
  if (tiles.length === 0) return null;
  return (
    <TileGroup caption={t.reachOuts.title} grid="grid-cols-2 md:grid-cols-3">
      {tiles.map((tile) => (
        <StatTile key={tile.id} label={tile.label} value={tile.value} />
      ))}
    </TileGroup>
  );
}

/**
 * 「好感度」里的「每日任务和成就」一小节：三项各做完几次，再是解锁的成就阶段、发出的抽奖次数、重置卡；
 * 为空时（好感度关着、范围内也没人做完或解锁）不显示。
 */
function TasksAndAchievementsTiles({ board }: { board: Dashboard }) {
  const tiles = [...dailyTaskTiles(board.daily_tasks), ...achievementTiles(board.achievements)];
  if (tiles.length === 0) return null;
  return (
    <TileGroup caption={t.tasksAndAchievements.title} grid="grid-cols-2 md:grid-cols-3">
      {tiles.map((tile) => (
        <StatTile key={tile.id} label={tile.label} value={tile.value} />
      ))}
    </TileGroup>
  );
}

/**
 * 「好感度」一组里好感度的部分（好感度生效时才有）：三张数字卡（进入冷却和涉及几位用户、不该说的话、操纵），
 * 打分分布（样子同「回合结果」），按角色的表（不并进「按角色」那张）。都不落到任何一位用户。
 */
function AffinityGroup({ section }: { section: AffinitySection }) {
  const words = t.affinity;
  const judged = section.levels.reduce((sum, n) => sum + n, 0);
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
        <StatTile
          label={words.tiles.cooldowns}
          value={formatCount(section.cooldowns)}
          note={words.tiles.cooledUsers(formatCount(section.cooled_users))}
        />
        <StatTile label={words.tiles.outOfLine} value={formatCount(section.out_of_line)} />
        <StatTile label={words.tiles.manipulation} value={formatCount(section.manipulation)} />
      </div>
      <CardPair>
        <DashboardCard title={words.levels.title}>
          <CountsTable
            rows={AFFINITY_LEVELS.map((level) => ({
              id: String(level),
              label: words.levels.names[level],
              count: section.levels[level] ?? 0,
            }))}
            total={judged}
            labelHeader={words.levels.level}
            countHeader={words.levels.turns}
            shareHeader={words.levels.share}
          />
        </DashboardCard>
      </CardPair>
      <DashboardCard title={words.characters.title}>
        <AffinityCharactersTable section={section} />
      </DashboardCard>
    </>
  );
}

/**
 * 按角色的好感度表：顺序是后端排好的（判断过的回合多的在前），默认收起；宽屏六列，加分和减分占比并排；
 * 更窄时每行末尾「加分 {x}% · 减分 {y}%」，其余放副行。
 */
function AffinityCharactersTable({ section }: { section: AffinitySection }) {
  const words = t.affinity.characters;
  if (section.characters.length === 0) return <p className="text-sm text-muted-foreground">{words.empty}</p>;
  const name = (row: AffinityRow) => row.name ?? <span className="text-muted-foreground">{t.characters.removed}</span>;
  return (
    <Folded rows={affinityRows(section.characters)}>
      {(shown) => (
        <TableOrRows
          table={
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{words.character}</TableHead>
                  <TableHead className="text-right">{words.judged}</TableHead>
                  <TableHead className="text-right">{words.raised}</TableHead>
                  <TableHead className="text-right">{words.lowered}</TableHead>
                  <TableHead className="text-right">{words.cooldowns}</TableHead>
                  <TableHead className="text-right">{words.sheEnded}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {shown.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell>{name(row)}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.judged}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.raised}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.lowered}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.cooldowns}</TableCell>
                    <TableCell className="text-right tabular-nums">{row.she_ended}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          }
          rows={
            <RowList
              rows={shown.map((row) => ({
                id: row.id,
                label: name(row),
                end: row.end,
                description: <span className="tabular-nums">{row.details}</span>,
              }))}
            />
          }
        />
      )}
    </Folded>
  );
}

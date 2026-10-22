"use client";

import { usePathname } from "next/navigation";
import { Fragment, type ReactNode } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { SettingsRow } from "@/components/settings/SettingsRow";
import { SettingsValuesProvider, useSettingsValues } from "@/components/settings/SettingsValues";
import { SponsorRow } from "@/components/settings/SponsorRow";
import { Separator } from "@/components/ui/separator";
import { copy } from "@/i18n";
import { settingsCategory, settingsGroups, settingsHref, shownCategory } from "@/lib/settingsCategory";
import { cn } from "@/lib/utils";
import {
  AboutIcon,
  AppearanceIcon,
  DataPackageIcon,
  ExperiencesIcon,
  FeaturesIcon,
  MemoriesIcon,
  OwnKeysIcon,
  PhoneIcon,
  ProfileIcon,
  ReachOutsIcon,
  SignInIcon,
  StatsIcon,
} from "@/theme/icons";

const t = copy.settings;

const ICONS = {
  profile: ProfileIcon,
  memories: MemoriesIcon,
  experiences: ExperiencesIcon,
  stats: StatsIcon,
  call: PhoneIcon,
  "reach-outs": ReachOutsIcon,
  features: FeaturesIcon,
  appearance: AppearanceIcon,
  "own-keys": OwnKeysIcon,
  "sign-in": SignInIcon,
  "data-package": DataPackageIcon,
  about: AboutIcon,
};

/**
 * 设置（DESIGN.md「设置页」）：一级是十类的列表，分四组、组间一条分隔线（部署者没开搭话时没有「搭话」，九类）；二级是一类的页面（`/settings/<类别>`），没有三级。
 * 部署者开着赞赏（GLOSSARY.md「赞赏」）时，最后一组「关于」上面多一行「赞赏」，不是一类、没有二级，点了放大收款码。
 * - 电脑：导航栏右边第二栏 320px 的一级列表，顶上「设置」大标题，当前那一类铺中性底色、不带 ›（网址就是这一类时才标 `aria-current`）；右栏是这一类的二级页面。
 *   只打开 `/settings` 时右栏是档案，档案是当前项。
 * - 手机：`/settings` 只露一级列表，每行末尾一个 ›；`/settings/<类别>` 只露二级页面，页首返回键回到一级。
 * 两种屏宽同一套路由，露哪一级只靠 CSS 断点，首屏不闪。
 * 一级每行名字下面是这一类的当前值摘要；各类的当前值和档案没保存的草稿挂在这一层，切类别时留着、离开设置就丢。
 */
export default function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <SettingsValuesProvider>
      <SettingsLevels>{children}</SettingsLevels>
    </SettingsValuesProvider>
  );
}

function SettingsLevels({ children }: { children: ReactNode }) {
  const { summaries } = useSettingsValues();
  const { account } = useDirectory();
  const sponsor = account?.sponsor_version ?? null;
  const pathname = usePathname();
  const named = settingsCategory(pathname);
  const isFirstLevel = named === null;
  const current = shownCategory(pathname);

  return (
    <div className="flex h-full bg-background text-foreground">
      <nav
        aria-label={t.title}
        className={cn(
          "flex w-80 shrink-0 flex-col border-r max-md:w-full max-md:border-r-0",
          !isFirstLevel && "max-md:hidden",
        )}
      >
        <header className="flex min-h-14 shrink-0 items-center pt-3 pr-2 pb-2 pl-4">
          <h1 className="text-list-title">{t.title}</h1>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto pb-4">
          {settingsGroups(account?.reach_outs ?? null).map((group, i) => (
            <Fragment key={group[0]}>
              {i > 0 && <Separator className="my-1" />}
              <ul>
                {group.map((category) => (
                  <Fragment key={category}>
                    {category === "about" && sponsor !== null && (
                      <li>
                        <SponsorRow version={sponsor} />
                      </li>
                    )}
                    <li>
                      <SettingsRow
                        href={settingsHref(category)}
                        label={t.categories[category]}
                        summary={summaries[category]}
                        Icon={ICONS[category]}
                        isCurrent={category === current}
                        isCurrentPage={category === named}
                      />
                    </li>
                  </Fragment>
                ))}
              </ul>
            </Fragment>
          ))}
        </div>
      </nav>
      <div className={cn("min-w-0 flex-1", isFirstLevel && "max-md:hidden")}>{children}</div>
    </div>
  );
}

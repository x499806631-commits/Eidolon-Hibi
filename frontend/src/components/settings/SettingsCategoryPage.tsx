import type { ReactNode } from "react";

import { AboutSettings } from "@/components/AboutSettings";
import { AdminEntry } from "@/components/AdminEntry";
import { AppearanceSettings } from "@/components/AppearanceSettings";
import { CallSettings } from "@/components/CallSettings";
import { DataPackageSettings } from "@/components/DataPackageSettings";
import { DeleteAccount } from "@/components/DeleteAccount";
import { ExperiencesSettings } from "@/components/ExperiencesSettings";
import { FeaturesSettings } from "@/components/FeaturesSettings";
import { MemoriesSettings } from "@/components/MemoriesSettings";
import { OwnKeysSettings } from "@/components/OwnKeysSettings";
import { ProducerProfileSettings } from "@/components/ProducerProfileSettings";
import { ReachOutsSettings } from "@/components/ReachOutsSettings";
import { SettingsPane } from "@/components/settings/SettingsPane";
import { SignInSettings } from "@/components/SignInSettings";
import { SignOutSettings } from "@/components/SignOutSettings";
import { StatsSettings } from "@/components/StatsSettings";
import { VoiceSettingsSection } from "@/components/VoiceSettingsSection";
import type { SettingsCategory } from "@/lib/settingsCategory";

/** 各类二级页面的内容，按 spec「设置：二级」。记忆和统计自己带页首末端的「清空」「分享」，整页由它们画。 */
const PANELS: Record<Exclude<SettingsCategory, "memories" | "experiences" | "stats">, ReactNode> = {
  profile: <ProducerProfileSettings />,
  call: (
    <>
      <CallSettings />
      <VoiceSettingsSection />
    </>
  ),
  "reach-outs": <ReachOutsSettings />,
  features: <FeaturesSettings />,
  appearance: <AppearanceSettings />,
  "own-keys": <OwnKeysSettings />,
  "sign-in": (
    <>
      <SignInSettings />
      <AdminEntry />
      <SignOutSettings />
      <DeleteAccount />
    </>
  ),
  "data-package": <DataPackageSettings />,
  about: <AboutSettings />,
};

/** 设置一类的二级页面。 */
export function SettingsCategoryPage({ category }: { category: SettingsCategory }) {
  if (category === "memories") return <MemoriesSettings />;
  if (category === "experiences") return <ExperiencesSettings />;
  if (category === "stats") return <StatsSettings />;
  return <SettingsPane category={category}>{PANELS[category]}</SettingsPane>;
}

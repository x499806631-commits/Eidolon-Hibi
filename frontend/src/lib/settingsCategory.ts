// The categories of the settings, each at a path of its own (.scratch/app-navigation spec
// 「设置：一级」「设置：网址」): `/settings` is the first level, the list of them; `/settings/<category>`
// is a category's page, the second level, with nothing below it. A refresh or a shared link opens
// the same category. The old addresses (`/settings?tab=…`, `/memories`) are sent to the new paths,
// and settings addresses we do not know to `/settings`. Each row of the first level shows the
// category's current value under its name (「设置：一级」). 「搭话」 is there only where the deployer
// has reach-outs on (.scratch/proactive-messages spec「前端」); its address elsewhere goes back to
// `/settings` from its page, as the server cannot tell the account from the address.

import { copy } from "@/i18n";
import type { Appearance } from "@/lib/appearance";
import type { CallPreferences } from "@/lib/callPreferences";
import { OWN_SERVICES, type OwnService } from "@/lib/localOwnKeys";
import type { Feature } from "@/lib/api";
import { sameDraft, type Draft } from "@/lib/producerProfile";
import { PROVIDER_LABELS, type Provider } from "@/lib/signIn";

/** The first level's groups, top to bottom, with a separator between each two. */
const SETTINGS_GROUPS = [
  ["profile", "memories", "experiences", "stats"],
  ["call", "reach-outs", "features", "appearance", "own-keys"],
  ["sign-in", "data-package"],
  ["about"],
] as const;

export const SETTINGS_CATEGORIES = SETTINGS_GROUPS.flat();

export type SettingsCategory = (typeof SETTINGS_CATEGORIES)[number];

const ROOT = "/settings";

/** The first level's groups for an account whose switch for reach-outs is `reachOuts` (null where
 * the deployer has them off, or the account not read yet): without 「搭话」 then. */
export function settingsGroups(reachOuts: boolean | null): SettingsCategory[][] {
  return SETTINGS_GROUPS.map((group) => group.filter((category) => category !== "reach-outs" || reachOuts !== null));
}

function isCategory(name: string | null | undefined): name is SettingsCategory {
  return SETTINGS_CATEGORIES.some((known) => known === name);
}

/** A link straight to a category, e.g. from the call's end. */
export function settingsHref(category: SettingsCategory): string {
  return `${ROOT}/${category}`;
}

/** The category whose page the path is, or null for the first level and anything else. */
export function settingsCategory(path: string): SettingsCategory | null {
  const match = /^\/settings\/([^/]+)$/.exec(path);
  return match && isCategory(match[1]) ? match[1] : null;
}

/** The category on show beside the list on a desktop: the first level alone shows the profile. */
export function shownCategory(path: string): SettingsCategory {
  return settingsCategory(path) ?? "profile";
}

/** Where an old or unknown settings address goes instead (replacing it in the history), or null
 * if it is fine as it is. The address's other parameters stay. */
export function settingsRedirect(href: string): string | null {
  const url = new URL(href, "http://settings.invalid");
  const path = url.pathname;
  const params = url.searchParams;
  let to: string;
  if (path === "/memories") to = settingsHref("memories");
  else if (path === ROOT && params.has("tab")) {
    const tab = params.get("tab");
    params.delete("tab");
    to = isCategory(tab) ? settingsHref(tab) : ROOT;
  } else if (path.startsWith(`${ROOT}/`) && settingsCategory(path) === null) to = ROOT;
  else return null;
  const search = params.toString();
  return search ? `${to}?${search}` : to;
}

/** The profile as saved, and as being edited in the profile's page (not yet saved). */
export type ProfileEdit = { saved: Draft; draft: Draft };

/** What the first level's summaries are made of; null for what has not been read yet. */
export type SettingsValues = {
  profile: ProfileEdit | null;
  /** The time zone this device reports, which an automatic profile time zone means. */
  detectedTimezone: string;
  memoryCount: number | null;
  /** The days since the user signed up (`/api/stats`). */
  daysKnown: number | null;
  call: CallPreferences | null;
  appearance: Appearance | null;
  /** The user's own switch for reach-outs (null where the deployer has them off). */
  reachOuts: boolean | null;
  /** The user's own switches for the fork's features the deployment has (GLOSSARY.md「玩法开关」). */
  features: Partial<Record<Feature, boolean>> | null;
  /** The services this browser keeps the user's own key for. */
  ownServices: OwnService[] | null;
  signInMethods: Provider[] | null;
};

/** The line under each category's name on the first level; a category without one (not read yet,
 * or「关于」) shows its name alone. */
export function settingsSummaries(values: SettingsValues): Partial<Record<SettingsCategory, string>> {
  const t = copy.settings.summaries;
  const { profile, memoryCount, daysKnown, call, appearance, reachOuts, features, ownServices, signInMethods } = values;
  const summaries: Partial<Record<SettingsCategory, string>> = {};
  if (profile) {
    const { saved, draft } = profile;
    const timezone = saved.timezone || copy.settings.profile.timezoneAuto(values.detectedTimezone);
    summaries.profile = sameDraft(draft, saved)
      ? `${saved.formOfAddress || t.addressDefault} · ${timezone}`
      : t.profileUnsaved;
  }
  if (memoryCount !== null) summaries.memories = memoryCount === 0 ? t.noMemories : t.memories(memoryCount);
  if (daysKnown !== null) summaries.stats = t.daysKnown(daysKnown);
  if (call) summaries.call = `${copy.settings.call.qualities[call.quality]} · ${call.phoneTexture ? t.phoneTextureOn : t.phoneTextureOff}`;
  if (appearance) summaries.appearance = appearance.scspAvatars ? t.scspAvatarsOn : t.scspAvatarsOff;
  if (reachOuts !== null) summaries["reach-outs"] = reachOuts ? t.reachOutsOn : t.reachOutsOff;
  if (features) {
    const off = Object.values(features).filter((on) => !on).length;
    summaries.features = off === 0 ? t.featuresAllOn : t.featuresOff(off);
  }
  if (ownServices) {
    const owned = OWN_SERVICES.filter((service) => ownServices.includes(service));
    summaries["own-keys"] =
      owned.length === 0 ? t.ownKeysNone : t.ownKeys(owned.map((service) => copy.ownServices[service]).join(copy.serviceList.separator));
  }
  if (signInMethods) summaries["sign-in"] = signInMethods.map((provider) => PROVIDER_LABELS[provider]).join("、");
  return summaries;
}

"use client";

import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { useDirectory } from "@/components/conversations/ConversationDirectory";
import { getProfile, getStats, listMemories } from "@/lib/api";
import { loadCallPreferences, type CallPreferences } from "@/lib/callPreferences";
import { toFailure, type Failure } from "@/lib/failure";
import { localOwnKeys, ownServices, subscribeOwnKeys, type OwnService } from "@/lib/localOwnKeys";
import { toDraft, type Draft } from "@/lib/producerProfile";
import { settingsSummaries, type ProfileEdit, type SettingsCategory } from "@/lib/settingsCategory";
import { detectTimezone } from "@/lib/timezone";

type CurrentSettings = {
  /** 一级每行名字下面的摘要，还没读到的那类没有。 */
  summaries: Partial<Record<SettingsCategory, string>>;
  /** 档案（已保存的和正在改的草稿）；读到之前是 null，读不到时 profileFailure 说原因。 */
  profile: ProfileEdit | null;
  profileFailure: Failure | null;
  /** 档案没读到时再读一次（档案页打开时）。 */
  retryProfile: () => void;
  /** 档案页里改了草稿，还没保存。 */
  editProfile: (draft: Draft) => void;
  /** 档案保存了：草稿和已保存的都换成服务器存下的。 */
  profileSaved: (saved: Draft) => void;
  /** 记忆页里的条数变了（删除、清空、重新取到）。 */
  memoryCountChanged: (count: number) => void;
  /** 通话页里改了通话偏好。 */
  callChanged: (call: CallPreferences) => void;
};

const SettingsValuesContext = createContext<CurrentSettings | null>(null);

/**
 * 设置各类的当前值，挂在设置的 layout 上（spec「设置：一级」「设置：没保存的档案草稿」）：一级列表的摘要从这里来，
 * 二级页面改了值就告诉这里，摘要马上跟着变（登录方式从外壳持有的登录的用户读，解绑后由登录与数据页换掉）。档案的草稿也在这里，在设置各类别之间切换时留着；离开设置 layout 卸载，草稿就丢了。
 */
export function SettingsValuesProvider({ children }: { children: ReactNode }) {
  const { account, appearance } = useDirectory();
  const userId = account?.id ?? null;
  const [profile, setProfile] = useState<ProfileEdit | null>(null);
  const [profileFailure, setProfileFailure] = useState<Failure | null>(null);
  const [detectedTimezone, setDetectedTimezone] = useState("");
  const [memoryCount, setMemoryCount] = useState<number | null>(null);
  const [daysKnown, setDaysKnown] = useState<number | null>(null);
  const [call, setCall] = useState<CallPreferences | null>(null);
  const [ownedServices, setOwnedServices] = useState<OwnService[] | null>(null);
  const [profileAttempt, setProfileAttempt] = useState(0);

  useEffect(() => {
    let current = true;
    getProfile().then(
      (saved) => current && setProfile({ saved: toDraft(saved), draft: toDraft(saved) }),
      (e) => current && setProfileFailure(toFailure(e)),
    );
    return () => {
      current = false;
    };
  }, [profileAttempt]);

  useEffect(() => {
    setDetectedTimezone(detectTimezone());
    setCall(loadCallPreferences());
    let current = true;
    // 只为摘要里的条数；取不到时那一行只显示名字。
    listMemories().then((memories) => current && setMemoryCount((count) => count ?? memories.length), () => {});
    getStats().then((stats) => current && setDaysKnown(stats.days_known), () => {});
    return () => {
      current = false;
    };
  }, []);

  useEffect(() => {
    if (userId === null) return;
    const read = () => setOwnedServices(ownServices(localOwnKeys(userId)));
    read();
    return subscribeOwnKeys(read);
  }, [userId]);

  const values = useMemo<CurrentSettings>(
    () => ({
      summaries: settingsSummaries({
        profile,
        detectedTimezone,
        memoryCount,
        daysKnown,
        call,
        // 外观和角色一起由外壳读出；登录的用户读到之前还是默认值，不算读到。
        appearance: account ? appearance : null,
        reachOuts: account?.reach_outs ?? null,
        features: account?.features ?? null,
        ownServices: ownedServices,
        signInMethods: account?.sign_in_methods.map((method) => method.provider) ?? null,
      }),
      profile,
      profileFailure,
      retryProfile: () => {
        setProfileFailure(null);
        setProfileAttempt((n) => n + 1);
      },
      editProfile: (draft) => setProfile((profile) => profile && { ...profile, draft }),
      profileSaved: (saved) => setProfile({ saved, draft: saved }),
      memoryCountChanged: setMemoryCount,
      callChanged: setCall,
    }),
    [profile, profileFailure, detectedTimezone, memoryCount, daysKnown, call, appearance, ownedServices, account],
  );

  return <SettingsValuesContext.Provider value={values}>{children}</SettingsValuesContext.Provider>;
}

export function useSettingsValues(): CurrentSettings {
  const values = useContext(SettingsValuesContext);
  if (!values) throw new Error("useSettingsValues outside SettingsValuesProvider");
  return values;
}

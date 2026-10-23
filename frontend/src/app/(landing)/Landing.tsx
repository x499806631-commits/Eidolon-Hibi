import { buttonVariants } from "@/components/ui/button";
import { copy } from "@/i18n";
import type { Character, LandingAssets, SignInOptions } from "@/lib/api";
import { REPOSITORY_URL } from "@/lib/repository";
import { PROVIDER_LABELS } from "@/lib/signIn";

import { Honesty, Start } from "./Closing";
import { LandingEntry } from "./LandingEntry";
import { LightStage } from "./LightStage";
import { Memory } from "./Memory";
import type { Actor } from "./model/memory";
import { lightsOf, prepareSpectrum, type Spectrum as SpectrumModel } from "./model/spectrum";
import { timelineOf } from "./model/timeline";
import { Rail } from "./Rail";
import { Spectrum, type SpectrumUnit } from "./Spectrum";
import { StaticLight } from "./StaticLight";
import { Voice, type Speaker } from "./Voice";

const t = copy.landing;

/**
 * 落地页（ADR-0016）：`/` 和 `/about` 共用。第 0 幕的标题、首屏声明和 CSS 画的白光；第 1 幕白光射进
 * 棱镜、散成一道道印象色（LightStage 加 OGL 光层，静态档是 StaticLight 的 SVG）；第 2 幕沿着光谱
 * 横向走过各个组合（Spectrum）；第 3 幕她说日语、第 4 幕她打来电话（Voice）；第 5 幕 3D 的夜空里，一件事从
 * 一位偶像传到另一位（Memory，three.js 的场景由 LightStage 加载）；第 6 幕安静的诚实事项、第 7 幕光汇聚成入口（Closing）；左侧的幕名轨（Rail）、
 * 右上角的仓库链接和入口，以及页脚。各幕的元素用 `data-lp-act` 标出它是第几幕，静止版式的幕名轨按它
 * 跟着滚动走。
 *
 * 页面按时间线（model/timeline.ts）分成几段，每段用 `data-lp-section` 标出它是第几段；钉住的一段高度
 * 是它占的屏数（landing.css 的 --lp-screens），舞台的循环按这些段把滚动位置换算成屏数。
 *
 * 标题是服务端渲染的 DOM 文字，从一开始就可见，不用透明度动画压住：它是首屏的 LCP 元素。
 * 文案、颜色和入口按钮的类名都在服务端算好，传给客户端组件的只有字符串和数：文案目录、颜色算法、
 * cn 不进落地页的包。
 */
export function Landing({
  isSignedIn,
  characters,
  signInOptions,
  assets,
}: {
  isSignedIn: boolean;
  characters: Character[];
  /** 后端的登录选项；取不到时第 6 幕不提登录方式。 */
  signInOptions: SignInOptions | null;
  /** 不属于哪一位角色的图（第 5 幕的静止图）；没有时只有文字。 */
  assets: LandingAssets;
}) {
  const spectrum = prepareSpectrum(characters);
  const lights = lightsOf(spectrum);
  const timeline = timelineOf(spectrum.units.length);
  const builtActs = new Set(timeline.acts.map((a) => a.act));
  const entryLabel = isSignedIn ? t.continue : t.signIn;
  const sectionOf = (act: number) => timeline.sections.find((s) => s.acts.includes(act))!;
  const units = unitsOf(spectrum, characters);
  const speaker = speakerOf(units, characters);
  const actors = actorsOf(units, characters);
  return (
    <div className="lp-root">
      <Rail label={t.rail.label} acts={t.rail.acts.flatMap((name, act) => (builtActs.has(act) ? [{ act, name }] : []))} />

      <header className="lp-top">
        <a href={REPOSITORY_URL} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: "ghost" })}>
          {copy.app.repositoryLabel}
        </a>
        <LandingEntry isSignedIn={isSignedIn} label={entryLabel} className={buttonVariants({ variant: "outline" })} />
      </header>

      <main>
        <section className="lp-act0" aria-labelledby="lp-title" data-lp-section={sectionOf(0).index} data-lp-act="0">
          <div className="lp-beam" aria-hidden />
          <div className="lp-act0-text">
            <h1 id="lp-title" className="lp-title">
              {copy.app.name}
            </h1>
            <p className="lp-lede">{copy.app.description}</p>
          </div>
          <p className="lp-disclaimer">{t.disclaimer}</p>
        </section>
        <LightStage lights={lights} section={sectionOf(1)} speaker={speaker?.id ?? null} actors={actors}>
          <section className="lp-act1" aria-labelledby={lights.length > 0 ? "lp-act1-title" : undefined} data-lp-act="1">
            <StaticLight lights={lights} />
            {lights.length > 0 && (
              <div className="lp-act1-copy" data-lp-find="act1">
                <p className="lp-act1-name" lang="ja" aria-hidden>
                  {t.act1.name}
                </p>
                <div>
                  <h2 id="lp-act1-title" className="lp-act1-title">
                    {t.act1.title(String(lights.length))}
                  </h2>
                  <p className="lp-act1-body">{t.act1.body}</p>
                </div>
              </div>
            )}
          </section>
          {spectrum.idols.length > 0 && <Spectrum units={units} copy={spectrumCopy(spectrum)} />}
        </LightStage>
        {speaker && (
          <Voice
            section={sectionOf(3)}
            speaker={speaker}
            talk={t.act3}
            call={t.act4}
          />
        )}
        <Memory section={sectionOf(5)} copy={t.act5} actors={actors} stillUrl={assets.memory_still_url} />
        <Honesty section={sectionOf(6)} title={t.act6.title} lines={honestyLines(signInOptions)} />
        <Start
          section={sectionOf(7)}
          title={t.act7.title}
          body={t.act7.body}
          isSignedIn={isSignedIn}
          label={entryLabel}
          className={buttonVariants({ size: "lg" })}
        />
      </main>

      {/* 用普通的 <a>：next/link 不进落地页首屏的包；隐私政策、使用条款在应用的根 layout 下，跨根 layout 本来就是整页加载。 */}
      <footer className="lp-footer">
        <p>{copy.legal.disclaimer}</p>
        <p className="lp-footer-links">
          <a href="/privacy">{copy.legal.privacy}</a>
          <a href="/terms">{copy.legal.terms}</a>
        </p>
      </footer>
    </div>
  );
}

/** 名牌要的那一份：名字、编号、颜色、她在光谱里的位置、默认衣装和招呼，都在服务端算好。 */
function unitsOf(spectrum: SpectrumModel, characters: Character[]): SpectrumUnit[] {
  const outfitOf = new Map(characters.map((c) => [c.id, c.stand?.default_outfit ?? null]));
  const greetingOf = new Map(characters.map((c) => [c.id, c.greeting]));
  // 没有组合的那一段（事务员はづき）的偶像编号不是 1–28 这样连着的，名牌接着组合里的人数往下编。
  const inUnits = spectrum.units.filter((unit) => unit.name !== null).reduce((n, unit) => n + unit.idols.length, 0);
  return spectrum.units.map((unit, u) => ({
    name: unit.name,
    plates: unit.idols.map((idol, slot) => ({
      id: idol.id,
      name: idol.name,
      number:
        unit.name === null
          ? String(inUnits + slot + 1).padStart(2, "0")
          : idol.idolId === null
            ? null
            : String(idol.idolId).padStart(2, "0"),
      unit: u,
      slot,
      unitSize: unit.idols.length,
      ink: idol.colors.ink,
      halo: idol.colors.halo,
      official: idol.colors.official,
      deep: idol.colors.deep,
      outfit: outfitOf.get(idol.id) ?? null,
      greeting: greetingOf.get(idol.id) ?? null,
    })),
  }));
}

/** 第 3、4 幕说话、打来电话的那一位：文案指定的那位（台词照她写的），不在名单里时是光谱里的第一位；连同她的开场白和卡图。 */
function speakerOf(units: SpectrumUnit[], characters: Character[]): (Speaker & { id: string }) | null {
  const plates = units.flatMap((u) => u.plates);
  const her = plates.find((p) => p.id === t.act3.speaker) ?? plates[0];
  if (!her) return null;
  const character = characters.find((c) => c.id === her.id);
  return { ...her, opener: character?.call_opener ?? null, poster: character?.call_poster_url ?? null };
}

/** 第 5 幕的演员：文案指定的几位里在名单上的，连同她站的岛、印象色和体素文件。 */
function actorsOf(units: SpectrumUnit[], characters: Character[]): Actor[] {
  const plates = new Map(units.flatMap((u) => u.plates).map((p) => [p.id, p]));
  const voxelsOf = new Map(characters.map((c) => [c.id, c.voxels_url]));
  return t.act5.cast.flatMap((id, island) => {
    const her = plates.get(id);
    return her ? [{ id, name: her.name, color: her.official, voxels: voxelsOf.get(id) ?? null, island }] : [];
  });
}

const spectrumCopy = (spectrum: SpectrumModel) => ({
  label: t.act2.label,
  jump: t.act2.jump,
  jumpTo: spectrum.units.map((u) => t.act2.jumpTo(u.name ?? t.act2.others)),
  others: t.act2.others,
  sound: t.act2.sound,
  soundOn: t.act2.soundOn,
  soundOff: t.act2.soundOff,
});

/**
 * 第 6 幕的几句话；登录方式从后端的登录选项读出来，一种都没有时说暂时不能登录。和登录组件（SignInChoices）
 * 说法一致：开着开发登录时不说不能登录；取不到登录选项时不提。
 */
function honestyLines(options: SignInOptions | null): string[] {
  const lines = [t.act6.unofficial, t.act6.limits, t.act6.ownKey, t.act6.memories, t.act6.affinity];
  if (!options || (options.providers.length === 0 && options.dev_login)) return lines;
  const providers = options.providers.map((p) => PROVIDER_LABELS[p]);
  return [...lines, providers.length > 0 ? t.act6.signIn(providers.join(` ${t.act6.or} `)) : t.act6.noSignIn];
}

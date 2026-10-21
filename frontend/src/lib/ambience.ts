// 环境音（ASMR 模式）：通话时垫在她声音下面的雨声、暖炉、海浪……文件在 public/ambience/（CC0，来源见那里的 CREDITS.md）。
//
// 一个场景是几条一直循环的底（bed），有的还有隔一阵随机响一下的点缀（accent，雷声、翻书声）。底用两份交叠的
// 播放首尾交叉淡化着接起来，循环时不会「咔」一声。ASMR 模式的通话接通时自动放上次选的场景，普通通话要自己点开；
// 她说话时整体压低一些，说完慢慢回来；挂断时淡出。选了哪个场景、音量多大记在这个浏览器里。
//
// 选「自动」时，接通后告诉后端（`ambience-auto`），由判断模型按时间、季节和聊的内容挑一类（`AmbienceKind`），
// 后端发来 `ambience` 消息时，从这一类的场景里放一个；同一类就接着放，不换。

/** 一条循环的底：文件名，和它在场景里的音量（0–1）。 */
type Bed = { file: string; gain: number };
/** 隔一阵随机响一次的点缀：文件名、音量，和两次之间隔多少秒（最少、最多）。 */
type Accent = { file: string; gain: number; every: [number, number] };

/** 判断模型挑的那一类（后端 `ambience.AmbienceKind`）；none 是安静，什么都不放。 */
export type AmbienceKind = "rain" | "storm" | "fireplace" | "waves" | "summer-night" | "study" | "cafe" | "none";

export type AmbienceScene = {
  id: string;
  label: string;
  icon: string;
  kind: Exclude<AmbienceKind, "none">;
  beds: Bed[];
  accent?: Accent;
};

/** 选了「自动」时记下的场景值。 */
export const AUTO = "auto";

export const AMBIENCE_SCENES: readonly AmbienceScene[] = [
  { id: "rain-1", label: "雨声 1", icon: "🌧️", kind: "rain", beds: [{ file: "rain-1.mp3", gain: 1 }] },
  { id: "rain-2", label: "雨声 2", icon: "🌧️", kind: "rain", beds: [{ file: "rain-2.mp3", gain: 1 }] },
  {
    id: "storm-1",
    label: "雷雨 1",
    icon: "⛈️",
    kind: "storm",
    beds: [
      { file: "rain-1.mp3", gain: 1 },
      { file: "thunder-1.mp3", gain: 0.7 },
    ],
  },
  {
    id: "storm-2",
    label: "雷雨 2",
    icon: "⛈️",
    kind: "storm",
    beds: [{ file: "rain-2.mp3", gain: 1 }],
    accent: { file: "thunder-2.mp3", gain: 0.7, every: [40, 90] },
  },
  { id: "fireplace-1", label: "暖炉 1", icon: "🔥", kind: "fireplace", beds: [{ file: "fireplace-1.mp3", gain: 1 }] },
  { id: "fireplace-2", label: "暖炉 2", icon: "🔥", kind: "fireplace", beds: [{ file: "fireplace-2.mp3", gain: 1 }] },
  { id: "waves-1", label: "海浪 1", icon: "🌊", kind: "waves", beds: [{ file: "waves-1.mp3", gain: 1 }] },
  { id: "waves-2", label: "海浪 2", icon: "🌊", kind: "waves", beds: [{ file: "waves-2.mp3", gain: 1 }] },
  { id: "summer-night", label: "夏夜", icon: "🦗", kind: "summer-night", beds: [{ file: "summer-night-1.mp3", gain: 1 }] },
  {
    id: "study-1",
    label: "书房 1",
    icon: "📖",
    kind: "study",
    beds: [{ file: "clock-1.mp3", gain: 0.6 }],
    accent: { file: "pages.mp3", gain: 0.8, every: [25, 70] },
  },
  {
    id: "study-2",
    label: "书房 2",
    icon: "📖",
    kind: "study",
    beds: [{ file: "clock-2.mp3", gain: 0.6 }],
    accent: { file: "pages.mp3", gain: 0.8, every: [25, 70] },
  },
  { id: "cafe", label: "咖啡店", icon: "☕", kind: "cafe", beds: [{ file: "cafe-1.mp3", gain: 1 }] },
];

export type AmbienceVolume = "quiet" | "medium" | "loud";

/** 每档音量：相对她的声音有多大。 */
export const AMBIENCE_VOLUMES: Record<AmbienceVolume, number> = { quiet: 0.12, medium: 0.25, loud: 0.45 };

/** 她说话时环境音压到原来的几成，和压下去、回来要多久（秒）。 */
const DUCK = { level: 0.45, down: 0.3, up: 1.2 };
/** 开始放、换场景、挂断时淡入淡出多久（秒）；底的首尾交叉淡化最多多久。 */
const FADE = 2;
const CROSSFADE = 3;

/** 记在这个浏览器里的：选的场景（null 是关，`AUTO` 是自动），和音量。 */
export type AmbienceChoice = { scene: string | null; volume: AmbienceVolume };

const STORAGE_KEY = "eidolon.ambience";
const DEFAULTS: AmbienceChoice = { scene: "rain-1", volume: "medium" };

export function sceneById(id: string | null): AmbienceScene | null {
  return AMBIENCE_SCENES.find((s) => s.id === id) ?? null;
}

export function loadAmbience(): AmbienceChoice {
  try {
    const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
    return {
      scene: stored.scene === null || stored.scene === AUTO || sceneById(stored.scene) ? stored.scene : DEFAULTS.scene,
      volume: stored.volume in AMBIENCE_VOLUMES ? stored.volume : DEFAULTS.volume,
    };
  } catch {
    return { ...DEFAULTS };
  }
}

function saveAmbience(choice: AmbienceChoice): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(choice));
  } catch {
    // 存不进去（隐私模式等）：只在这次页面里生效。
  }
}

/** 判断模型挑了 `kind`，正在放的是 `playing`：要换成的场景，null 是停下，undefined 是不动（同一类接着放）。
 * 同一类有几个场景时随机放一个（`random` 是 0 到 1）。 */
export function sceneForKind(
  kind: AmbienceKind,
  playing: string | null,
  random: number,
): string | null | undefined {
  if (kind === "none") return null;
  if (sceneById(playing)?.kind === kind) return undefined;
  const scenes = AMBIENCE_SCENES.filter((s) => s.kind === kind);
  if (scenes.length === 0) return undefined;
  return scenes[Math.min(scenes.length - 1, Math.floor(random * scenes.length))].id;
}

/** 点缀下一次隔多少秒响：在 `every` 之间，`random` 是 0 到 1。 */
export function accentDelay(every: [number, number], random: number): number {
  return every[0] + (every[1] - every[0]) * random;
}

/** 通话画面上的按钮读到的：记下的选择，现在正在放的场景（没在放是 null），和这通电话是不是在自动挑。 */
export type AmbienceState = AmbienceChoice & { playing: string | null; auto: boolean; picked: AmbienceKind | null };

type Playing = { scene: AmbienceScene; out: GainNode; stops: (() => void)[] };

export class Ambience {
  private context: AudioContext | null = null;
  private master: GainNode | null = null;
  private readonly buffers = new Map<string, Promise<AudioBuffer | null>>();
  private current: Playing | null = null;
  private inCall = false;
  /** 这通电话里该不该放：ASMR 模式一接就放，普通通话在点选了以后。 */
  private active = false;
  /** 自动时判断模型最近挑的那一类；还没挑是 null。 */
  private autoKind: AmbienceKind | null = null;
  /** 接通后告诉后端要不要自动挑；没接通是 null。 */
  private tellAuto: ((on: boolean) => void) | null = null;
  private toldAuto = false;
  private ducked = false;
  private state: AmbienceState;
  private readonly listeners = new Set<() => void>();

  constructor(
    private readonly audioContext: () => AudioContext,
    private readonly base = "/ambience/",
    private readonly random: () => number = Math.random,
  ) {
    this.state = { ...loadAmbience(), playing: null, auto: false, picked: null };
  }

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };

  readonly snapshot = (): AmbienceState => this.state;

  /** 拨出一通电话时调，在用户这一下点击里：ASMR 模式的通话放上次选的场景（自动的等判断模型挑）。 */
  callStarted(asmr: boolean): void {
    this.inCall = true;
    this.active = asmr;
    this.autoKind = null;
    this.tellAuto = null;
    this.toldAuto = false;
    this.ducked = false;
    this.stop();
    this.apply();
  }

  /** 接通了：`tell` 告诉后端要不要自动挑，现在说一次，之后变了再说。 */
  connected(tell: (on: boolean) => void): void {
    if (!this.inCall) return;
    this.tellAuto = tell;
    this.toldAuto = false;
    this.syncAuto();
  }

  /** 后端说判断模型挑了 `kind`：自动时换过去。 */
  picked(kind: AmbienceKind): void {
    if (!this.inCall) return;
    this.autoKind = kind;
    this.apply();
  }

  /** 通话结束：淡出。 */
  callEnded(): void {
    this.inCall = false;
    this.active = false;
    this.tellAuto = null;
    this.toldAuto = false;
    this.stop();
    this.publish(this.state);
  }

  /** 选一个场景（null 是关，`AUTO` 是自动），记下来；通话中马上换过去。自动的先接着放正在放的，等判断模型挑。 */
  choose(scene: string | null): void {
    this.update({ scene });
    if (!this.inCall) return;
    this.active = scene !== null;
    if (scene === AUTO) this.autoKind = null;
    this.apply();
    this.syncAuto();
  }

  setVolume(volume: AmbienceVolume): void {
    this.update({ volume });
    this.applyLevel(FADE / 2);
  }

  /** 她开始说话（压低）或者说完了（慢慢回来）。 */
  duck(speaking: boolean): void {
    this.ducked = speaking;
    this.applyLevel(speaking ? DUCK.down : DUCK.up);
  }

  /** 页面离开：停掉，音频上下文由她的声音那边关。 */
  dispose(): void {
    this.inCall = false;
    this.stop(0);
    this.master = null;
    this.context = null;
  }

  private wantsAuto(scene = this.state.scene): boolean {
    return this.inCall && this.active && scene === AUTO;
  }

  /** 要不要自动挑变了就告诉后端。 */
  private syncAuto(): void {
    const want = this.wantsAuto();
    if (!this.tellAuto || want === this.toldAuto) return;
    this.toldAuto = want;
    this.tellAuto(want);
  }

  /** 按现在的选择放：没在通话里、这通还不该放、或者关了就停；自动的放判断模型挑的那一类。 */
  private apply(): void {
    const scene = this.state.scene;
    if (!this.inCall || !this.active || !scene) this.stop();
    else if (scene !== AUTO) this.start(scene);
    else if (this.autoKind !== null) {
      // 还没挑时正在放的接着放
      const next = sceneForKind(this.autoKind, this.current?.scene.id ?? null, this.random());
      if (next === null) this.stop();
      else if (next !== undefined) this.start(next);
    }
    this.publish(this.state);
  }

  private update(change: Partial<AmbienceChoice>): void {
    const { playing, auto, picked, ...choice } = { ...this.state, ...change };
    saveAmbience(choice);
    this.publish({ ...choice, playing, auto, picked });
  }

  private publish(state: AmbienceState): void {
    const auto = this.wantsAuto(state.scene);
    const picked = auto ? this.autoKind : null;
    if (state === this.state && auto === state.auto && picked === state.picked) return;
    this.state = { ...state, auto, picked };
    for (const listener of this.listeners) listener();
  }

  private ensureMaster(): { context: AudioContext; master: GainNode } {
    const context = this.audioContext();
    if (this.context !== context || !this.master) {
      this.context = context;
      this.master = context.createGain();
      this.master.gain.value = this.level();
      this.master.connect(context.destination);
    }
    if (context.state === "suspended") void context.resume().catch(() => {});
    return { context, master: this.master };
  }

  private level(): number {
    return AMBIENCE_VOLUMES[this.state.volume] * (this.ducked ? DUCK.level : 1);
  }

  private applyLevel(seconds: number): void {
    if (!this.master || !this.context) return;
    const now = this.context.currentTime;
    const gain = this.master.gain;
    gain.cancelScheduledValues(now);
    gain.setValueAtTime(gain.value, now);
    gain.linearRampToValueAtTime(this.level(), now + seconds);
  }

  private start(id: string): void {
    const scene = sceneById(id);
    if (!scene) return;
    if (this.current?.scene.id === id) return;
    this.stop();
    const { context, master } = this.ensureMaster();
    const out = context.createGain();
    out.gain.value = 1;
    out.connect(master);
    const playing: Playing = { scene, out, stops: [] };
    this.current = playing;
    for (const bed of scene.beds) playing.stops.push(this.loop(context, bed, out));
    if (scene.accent) playing.stops.push(this.accents(context, scene.accent, out));
    this.publish({ ...this.state, playing: id });
  }

  private stop(fade = FADE): void {
    const playing = this.current;
    this.current = null;
    if (playing) {
      for (const stop of playing.stops) stop();
      if (this.context) {
        const now = this.context.currentTime;
        playing.out.gain.cancelScheduledValues(now);
        playing.out.gain.setValueAtTime(playing.out.gain.value, now);
        playing.out.gain.linearRampToValueAtTime(0, now + fade);
      }
      setTimeout(() => playing.out.disconnect(), fade * 1000 + 100);
    }
    if (this.state.playing !== null) this.publish({ ...this.state, playing: null });
  }

  private buffer(context: AudioContext, file: string): Promise<AudioBuffer | null> {
    let loading = this.buffers.get(file);
    if (!loading) {
      loading = fetch(this.base + file)
        .then((res) => (res.ok ? res.arrayBuffer() : Promise.reject(new Error(res.statusText))))
        .then((data) => context.decodeAudioData(data))
        .catch(() => null);
      this.buffers.set(file, loading);
    }
    return loading;
  }

  /** 一条底：一份播到快结束时，下一份淡入接上，两份交叠着交叉淡化。返回停下它的函数（之后不再接下一份；
   * 正在放的跟着场景一起淡出）。 */
  private loop(context: AudioContext, bed: Bed, out: AudioNode): () => void {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    void this.buffer(context, bed.file).then((buffer) => {
      if (!buffer || stopped) return;
      const overlap = Math.min(CROSSFADE, buffer.duration / 3);
      const play = (fadeIn: number) => {
        const gain = context.createGain();
        gain.connect(out);
        const source = context.createBufferSource();
        source.buffer = buffer;
        source.connect(gain);
        const now = context.currentTime;
        const end = now + buffer.duration;
        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(bed.gain, now + fadeIn);
        gain.gain.setValueAtTime(bed.gain, end - overlap);
        gain.gain.linearRampToValueAtTime(0, end);
        source.start(now);
        source.stop(end + FADE);
        source.onended = () => gain.disconnect();
        timer = setTimeout(() => !stopped && play(overlap), (buffer.duration - overlap) * 1000);
      };
      play(FADE);
    });
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }

  /** 点缀：隔一阵随机响一次。 */
  private accents(context: AudioContext, accent: Accent, out: AudioNode): () => void {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const next = () => {
      timer = setTimeout(async () => {
        const buffer = await this.buffer(context, accent.file);
        if (stopped) return;
        if (buffer) {
          const gain = context.createGain();
          gain.gain.value = accent.gain;
          gain.connect(out);
          const source = context.createBufferSource();
          source.buffer = buffer;
          source.connect(gain);
          source.onended = () => gain.disconnect();
          source.start();
        }
        next();
      }, accentDelay(accent.every, this.random()) * 1000);
    };
    next();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }
}

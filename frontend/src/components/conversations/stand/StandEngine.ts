import type { Outfit } from "@/lib/api";
import { CALM, pickAnimations, type Emotion, type StandDirection } from "@/lib/standAnimations";
import { FrameGate, MAX_PIXEL_RATIO, pixelRatio } from "@/lib/standFrames";
import { partAt, reactionTo, type Body, type TouchPart } from "@/lib/standTouch";

import type {
  AnimationState,
  Bone,
  AssetManager,
  ClippingAttachment,
  SceneRenderer,
  Skeleton,
  SkeletonData,
  SlotData,
  Slot,
  SpineRuntime,
  TextureAtlas,
  TrackEntry,
} from "./spineRuntime";

/**
 * 取景（spec「渲染」）：手机「大立绘」贴着头顶、到膝；电脑「半身 ADV」贴着头顶、到腰；
 * 换衣装的大预览是全身。
 */
export type Framing = "knees" | "waist" | "full";

/** 取景的高度，占身体的比例（全身另算，见 `aim`）。 */
const FRAMING_HEIGHT: Record<Exclude<Framing, "full">, number> = { knees: 0.85, waist: 0.66 };
/** 取景的宽度不窄于身体的这个比例，免得竖屏上只剩一张脸。 */
const MIN_WIDTH = 0.7;
/** 头顶上方留的空（全身时四周留的空），占身体高度的比例。 */
const HEADROOM = 0.03;

/** 轨道照原作 ADV：0 身体、1 表情、2 点头、3 视线、4 脸红（只有被摸时用）、5 口型。 */
const TRACK = { body: 0, face: 1, nod: 2, eye: 3, blush: 4, mouth: 5 } as const;
/** 被摸以后的表情和脸红留多久（毫秒），之后回到立绘指令要的样子。 */
const TOUCH_FACE_MS = 2600;
/** 头的半径占整个人高度的比例（找不到头的骨骼时按这个从头顶往下估）。 */
const HEAD_RADIUS = 0.08;
/** 当成头的骨骼，按这个顺序找；都没有就找名字里带 head 的。 */
const HEAD_BONES = ["head2", "head", "face_control", "face"];
/** 同一条轨道上换动画时的混合时间（秒）。 */
const MIX_SECONDS = 0.3;
/** 开嘴、闭嘴的混合时间（秒）。 */
const MOUTH_MIX_SECONDS = 0.1;
/** 一帧最多推进这么久（秒）：卡了一下或者刚恢复时，动作不会一下子跳过去。 */
const MAX_STEP_SECONDS = 0.1;

const UNPACK_PREMULTIPLY_ALPHA_WEBGL = 0x9241;

/** 这台设备上画不了立绘：浏览器不支持 WebGL。 */
export class StandUnavailable extends Error {}

/** 载入好的一套衣装：骨架、它的动画状态和这套衣装有哪些动画。 */
type Pose = {
  /** 这套衣装的素材目录，释放素材时按它找。 */
  url: string;
  skeleton: Skeleton;
  data: SkeletonData;
  state: AnimationState;
  /** 这套衣装的动画，名字 → 时长（秒）。 */
  animations: ReadonlyMap<string, number>;
  /** 初始姿势下整个人的范围，取景据此计算。 */
  bounds: { x: number; y: number; width: number; height: number };
  /** 见过的剪裁，按剪裁附件记下它原本的结束插槽（见 `keepClipsEnding`）。 */
  clips: Map<ClippingAttachment, Clip>;
  /** 各条轨道现在放的是什么，只在要放的变了时才重设。 */
  shown: Shown;
};

type Shown = {
  /** 身体动作按哪种心情选的：心情变了才换身体动作。 */
  mood: Emotion | null;
  face: string | null;
  /** 口型的名字和开合，例如 `lip_smile open`。 */
  mouth: string | null;
  eye: string | null;
  /** 最近一次点头的编号。 */
  nod: number | null;
};

type Clip = { slot: Slot; end: SlotData; detached: boolean };

/**
 * 通话层上的立绘（spec「渲染」）：在一块画布上载入一套衣装，每帧推进动画并绘制。不依赖 React。
 *
 * 各条轨道按立绘指令放「选动画」（`pickAnimations`）给出的动画。runtime 的几个已知问题都在这里处理：
 * 剪裁不结束、预乘 alpha、上下文丢失后恢复、React 开发模式下同一块画布上重建引擎。像素比不超过 2、
 * 最多 60 帧，暂停时（通话层收起）不画。
 */
export class StandEngine {
  private readonly gl: WebGLRenderingContext;
  private readonly renderer: SceneRenderer;
  private readonly assets: AssetManager;
  private readonly listeners = new AbortController();
  private readonly gate = new FrameGate();
  private pose: Pose | null = null;
  private raf = 0;
  private last = 0;
  private paused = false;
  /** 静帧：画出一帧就停（落地页的「减少动态效果」档）。 */
  private still = false;
  private lost = false;
  private disposed = false;
  private direction: StandDirection = CALM;
  private reducedMotion = false;
  /** 第几次要求载入衣装：只有最后一次要的那套会换上。 */
  private loads = 0;
  /** 最后一次要的那套衣装的素材目录。 */
  private wanted: string | null = null;
  /** 正在下载的衣装，按素材目录。 */
  private readonly fetching = new Map<string, Promise<void>>();
  /** 上一帧的镜头：中心和看到的宽高（世界坐标），摸的时候据此把点换算到她身上。 */
  private view: { x: number; y: number; width: number; height: number } | null = null;
  private touchTimer = 0;

  /**
   * 在 `canvas` 上准备好绘制；浏览器不支持 WebGL 时抛出 `StandUnavailable`。
   * `framing` 可以随时改，下一帧起按新的取景画。
   */
  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly spine: SpineRuntime,
    public framing: Framing,
    /** 画布的像素比上限：落地页要的比通话层低。 */
    private readonly maxPixelRatio: number = MAX_PIXEL_RATIO,
  ) {
    const signal = this.listeners.signal;
    // 要注册在创建 ManagedWebGLRenderingContext 之前：恢复时浏览器按注册顺序调用监听，
    // 先重新开启「上传时预乘 alpha」，runtime 自己的监听再重新上传贴图，否则恢复后会出白边。
    // 恢复不了时画布一直是空的，通话层上就只剩背景图和纱，这次通话不再重试。
    // runtime 自己的监听销毁时摘不掉，但它管的贴图和着色器在销毁时都已移出恢复清单，留着也不会再上传什么。
    canvas.addEventListener(
      "webglcontextlost",
      () => {
        this.lost = true;
        this.stop();
      },
      { signal },
    );
    canvas.addEventListener(
      "webglcontextrestored",
      () => {
        this.gl.pixelStorei(UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
        this.lost = false;
        this.start();
      },
      { signal },
    );
    const context = new spine.webgl.ManagedWebGLRenderingContext(canvas, { alpha: true, premultipliedAlpha: true });
    if (!context.gl) {
      this.listeners.abort();
      throw new StandUnavailable("浏览器不支持 WebGL");
    }
    this.gl = context.gl;
    this.lost = this.gl.isContextLost();
    // 贴图上传时预乘 alpha，绘制时也按预乘处理，否则部分衣装脸上有白边、关节处有细线。
    this.gl.pixelStorei(UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
    this.renderer = new spine.webgl.SceneRenderer(canvas, context, true);
    this.assets = new spine.webgl.AssetManager(context);
  }

  /**
   * 按立绘指令摆姿势（spec「选动画」）。立绘还没到时先记下，到了直接按最新的指令开始画。
   * 减少动态效果从下一个身体动作（心情变了时）开始生效，不打断正在放的。
   */
  direct(direction: StandDirection, reducedMotion: boolean): void {
    this.direction = direction;
    this.reducedMotion = reducedMotion;
    if (this.pose) this.follow(this.pose);
  }

  /**
   * 载入一套衣装，按当前的立绘指令摆好姿势后开始绘制；已经穿着另一套时，换上这一套并释放上一套的素材。
   * 素材加载不到、数据读不了时 reject，身上那套照旧。接连换了几套时只换上最后要的那一套，
   * 前面几套到了也不换，素材直接释放。
   */
  async load(outfit: Outfit): Promise<void> {
    const { url } = outfit;
    const ticket = ++this.loads;
    this.wanted = url;
    if (this.pose?.url === url) return;
    try {
      await this.fetchOutfit(url);
    } catch (error) {
      // 同一套的每次载入共用一次下载，一起失败，素材释放一次就够。销毁之后才失败的，把陆续到的也释放掉。
      if (this.disposed) this.assets.dispose();
      else this.release(url);
      throw error;
    }
    if (this.disposed) {
      // 销毁之后才到的素材也要释放（再调一次 dispose 只会处理这些新到的）。
      this.assets.dispose();
      return;
    }
    if (ticket !== this.loads) {
      // 又要了别的衣装。要的还是这一套时，后来的那次载入会用上这些素材。
      if (this.wanted !== url) this.release(url);
      return;
    }
    let pose: Pose;
    try {
      pose = this.read(url);
    } catch (error) {
      this.release(url);
      throw error;
    }
    const previous = this.pose;
    this.pose = pose;
    if (previous) this.release(previous.url);
    this.follow(this.pose);
    this.start();
  }

  /** 读出已经下载好的一套衣装：骨架摆成初始姿势，量出整个人的范围。数据读不了时抛出。 */
  private read(url: string): Pose {
    const json = `${url}data.json`;
    const atlas = `${url}data.atlas`;
    const spine = this.spine;
    const loader = new spine.AtlasAttachmentLoader(this.assets.get(atlas) as TextureAtlas);
    const data = new spine.SkeletonJson(loader).readSkeletonData(this.assets.get(json) as string);
    const skeleton = new spine.Skeleton(data);
    if (data.findSkin("normal")) skeleton.setSkinByName("normal");
    skeleton.setToSetupPose();
    skeleton.updateWorldTransform();
    const offset = new spine.Vector2();
    const size = new spine.Vector2();
    skeleton.getBounds(offset, size, []);
    const stateData = new spine.AnimationStateData(data);
    stateData.defaultMix = MIX_SECONDS;
    return {
      url,
      skeleton,
      data,
      state: new spine.AnimationState(stateData),
      animations: new Map(data.animations.map((a) => [a.name, a.duration])),
      bounds: { x: offset.x, y: offset.y, width: size.x, height: size.y },
      clips: new Map(),
      // 立绘到之前的点头已经过去了，不补。
      shown: { mood: null, face: null, mouth: null, eye: null, nod: this.direction.nod?.id ?? null },
    };
  }

  /**
   * 下载一套衣装的 `data.json` 和 `data.atlas`（连同图集里的贴图）。同一套正在下载时共用这一次。
   * 等两份都有了结果再往下走：先失败的那份不能让另一份在引擎销毁之后才把贴图传上去。
   */
  private fetchOutfit(url: string): Promise<void> {
    let fetching = this.fetching.get(url);
    if (!fetching) {
      fetching = Promise.allSettled([
        new Promise<void>((resolve, reject) =>
          this.assets.loadText(`${url}data.json`, () => resolve(), (_, message) => reject(new Error(message))),
        ),
        new Promise<void>((resolve, reject) =>
          this.assets.loadTextureAtlas(`${url}data.atlas`, () => resolve(), (_, message) => reject(new Error(message))),
        ),
      ])
        .then((results) => {
          for (const result of results) if (result.status === "rejected") throw result.reason;
        })
        .finally(() => this.fetching.delete(url));
      this.fetching.set(url, fetching);
    }
    return fetching;
  }

  /**
   * 释放一套衣装的素材（按素材目录找到的文字、图集和贴图）。3.6 的 `AssetManager.remove` 只把条目设成 null，
   * 之后销毁时 `removeAll` 会对 null 调 dispose 报错，所以这里自己 dispose，再连条目一起删掉。
   * 贴图 dispose 时会把自己移出 `ManagedWebGLRenderingContext.restorables`，上下文恢复时不会把旧衣装再传一遍；
   * 图集 dispose 时已经释放过它的贴图，贴图再 dispose 一次只是重复删除，WebGL 会忽略。
   */
  private release(url: string): void {
    const assets = this.assets.assets;
    for (const path of Object.keys(assets)) {
      if (!path.startsWith(url)) continue;
      const asset = assets[path];
      if (typeof asset === "object" && asset) asset.dispose?.();
      delete assets[path];
    }
  }

  /** 静帧：只画一帧初始姿势就停下，之后换衣装也只再画一帧。在载入衣装之前定下。 */
  setStill(still: boolean): void {
    this.still = still;
  }

  /** 暂停（通话层收起时）不画，也不推进动画；恢复后从当前姿势接着画。 */
  setPaused(paused: boolean): void {
    this.paused = paused;
    if (paused) this.stop();
    else this.start();
  }

  /**
   * 停止绘制并释放素材。不调用 `loseContext()`：React 开发模式下 effect 会执行两次，
   * 在同一块画布上重建引擎时拿到的还是同一个上下文，丢掉它新引擎就画不出来了。
   */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    clearTimeout(this.touchTimer);
    this.stop();
    this.listeners.abort();
    this.pose = null;
    this.assets.dispose();
    this.renderer.dispose();
  }

  /**
   * 点在画布上 (`x`, `y`)（CSS 像素，相对画布左上角）碰到了她的哪儿（FORK.md「立绘触摸」）；
   * 还没画出来、没碰到她时是 null。
   */
  touchedAt(x: number, y: number): TouchPart | null {
    const pose = this.pose;
    const view = this.view;
    const { clientWidth, clientHeight } = this.canvas;
    if (!pose || !view || !clientWidth || !clientHeight) return null;
    const at = {
      x: view.x + (x / clientWidth - 0.5) * view.width,
      y: view.y + (0.5 - y / clientHeight) * view.height,
    };
    return partAt(at, this.bodyOf(pose));
  }

  /** 她身上能摸的几处，这一帧在哪儿。 */
  private bodyOf({ skeleton, bounds }: Pose): Body {
    const radius = bounds.height * HEAD_RADIUS;
    const top = bounds.y + bounds.height;
    const named = (bone: Bone) => bone.data.name.toLowerCase();
    const headBone =
      HEAD_BONES.map((name) => skeleton.findBone(name)).find((b): b is Bone => b !== null) ??
      skeleton.bones.find((b) => named(b).includes("head")) ??
      null;
    // 头的骨骼多在脖子上，只取它的左右；上下从头顶往下一个半径。
    const head = { x: headBone?.worldX ?? bounds.x + bounds.width / 2, y: top - radius * 1.1 };
    const hands = skeleton.bones.filter((b) => /hand|te_[lr]/.test(named(b))).map((b) => ({ x: b.worldX, y: b.worldY }));
    return { head, radius, hands };
  }

  /**
   * 被摸了一下（FORK.md「立绘触摸」）：马上换上反应的动作和表情，脸红一下；`pestered` 是一直在戳她，
   * 换成生气。过一会儿回到立绘指令要的样子。减少动态效果时只换表情，不做身体动作。
   */
  react(part: TouchPart, pestered: boolean): void {
    const pose = this.pose;
    if (!pose) return;
    const { state, animations, shown } = pose;
    const reaction = reactionTo(part, pestered, (name) => animations.has(name));
    if (reaction.body && !this.reducedMotion) {
      const entry = state.setAnimation(TRACK.body, reaction.body, false);
      entry.listener = {
        complete: () => {
          // 做完了：回到心情要的身体动作。
          shown.mood = null;
          if (this.pose === pose) this.follow(pose);
        },
      };
    }
    if (reaction.face) {
      state.setAnimation(TRACK.face, reaction.face, true);
      shown.face = reaction.face;
    }
    if (reaction.blush) state.setAnimation(TRACK.blush, "cheek_red", false);
    clearTimeout(this.touchTimer);
    this.touchTimer = window.setTimeout(() => {
      if (this.pose !== pose) return;
      shown.face = null;
      state.setEmptyAnimation(TRACK.blush, MIX_SECONDS);
      this.follow(pose);
    }, TOUCH_FACE_MS);
  }

  /**
   * 让各条轨道放选出来的动画，只动要放的变了的轨道。衣装缺了选出来的动画（例如 `wait`、`blank`）时，
   * 这条轨道保持原样。
   */
  private follow(pose: Pose): void {
    const { state, shown } = pose;
    const want = pickAnimations(this.direction, pose.animations, this.reducedMotion);
    if (shown.mood !== this.direction.mood) {
      shown.mood = this.direction.mood;
      this.playBody(pose, want.body);
    }
    if (shown.face !== want.face) {
      shown.face = want.face;
      // 衣装连 `blank` 都没有时清空这条轨道，同样把脸交还给身体动作。
      if (pose.animations.has(want.face)) state.setAnimation(TRACK.face, want.face, true);
      else state.setEmptyAnimation(TRACK.face, MIX_SECONDS);
    }
    const mouth = want.mouth && `${want.mouth.name} ${want.mouth.open ? "open" : "shut"}`;
    if (want.mouth && shown.mouth !== mouth) {
      shown.mouth = mouth;
      // 闭嘴照原作：把同一个说话口型冻结在第 0 帧。不用 `lip_*_s`：它不管嘴部网格的变形，
      // 身体动作里的嘴部变形会透出来，停了嘴还在微微动。冻结用 `animationEnd = 0` 而不是
      // `timeScale = 0`：3.6 的混合进度按 timeScale 走，为 0 时混合永远走不完，上一段说话口型会一直在底下播。
      const entry = state.setAnimation(TRACK.mouth, want.mouth.name, want.mouth.open);
      entry.mixDuration = MOUTH_MIX_SECONDS;
      if (!want.mouth.open) entry.animationEnd = 0;
    }
    if (shown.eye !== want.eye) {
      shown.eye = want.eye;
      if (want.eye) state.setAnimation(TRACK.eye, want.eye, true);
      else state.setEmptyAnimation(TRACK.eye, MIX_SECONDS);
    }
    if (want.nod && shown.nod !== want.nod.id) {
      shown.nod = want.nod.id;
      // 播一次就清空这条轨道：`yes` 播完时不在中立姿势。播完才开始清空：3.6 的 addEmptyAnimation
      // 在 delay 为 0 时会把混合时间扣两次，点头的最后 0.6 秒就没了。
      const nod = state.setAnimation(TRACK.nod, want.nod.name, false);
      state.addEmptyAnimation(TRACK.nod, MIX_SECONDS, nod.animation.duration);
    }
  }

  /**
   * 换身体动作。带 `loop_start` 事件的，开头播一次，之后从 `loop_start` 起循环尾段；没有的整段循环。
   * 离开带 `relay` 事件的动作时，先播它指定的 `arm_down*` 把抬起的手放下，再接下一个动作。
   */
  private playBody({ state, data, animations }: Pose, name: string): void {
    if (!animations.has(name)) return;
    const current = state.getCurrent(TRACK.body);
    if (current?.animation.name === name) return;
    const relay = current && this.event(data, current.animation.name, "relay");
    let entry: TrackEntry;
    if (relay && animations.has(relay.value)) {
      state.setAnimation(TRACK.body, relay.value, false);
      entry = state.addAnimation(TRACK.body, name, false, 0);
    } else {
      entry = state.setAnimation(TRACK.body, name, false);
    }
    const loopStart = this.event(data, name, "loop_start");
    if (!loopStart) {
      entry.loop = true;
      return;
    }
    entry.listener = {
      complete: (played) => {
        if (played.loop) return;
        played.loop = true;
        played.animationStart = loopStart.time;
        played.trackTime = 0;
      },
    };
  }

  /** 动画 `name` 里第一个叫 `eventName` 的事件：它的时间和字符串参数。 */
  private event(data: SkeletonData, name: string, eventName: string): { time: number; value: string } | null {
    for (const timeline of data.findAnimation(name)?.timelines ?? []) {
      if (!(timeline instanceof this.spine.EventTimeline)) continue;
      for (const event of timeline.events)
        if (event.data.name === eventName)
          return { time: event.time, value: event.stringValue || event.data.stringValue || "" };
    }
    return null;
  }

  private start(): void {
    if (this.raf || !this.pose || this.paused || this.lost || this.disposed) return;
    this.last = 0;
    this.raf = requestAnimationFrame(this.frame);
  }

  private stop(): void {
    cancelAnimationFrame(this.raf);
    this.raf = 0;
  }

  private readonly frame = (now: number): void => {
    this.raf = requestAnimationFrame(this.frame);
    if (!this.gate.shouldDraw(now)) return;
    const step = this.last ? Math.min((now - this.last) / 1000, MAX_STEP_SECONDS) : 0;
    this.last = now;
    if (this.pose) this.draw(this.pose, step);
    if (this.still) this.stop();
  };

  private draw(pose: Pose, step: number): void {
    const { canvas, gl, renderer } = this;
    const ratio = pixelRatio(window.devicePixelRatio, this.maxPixelRatio);
    const width = Math.max(1, Math.round(canvas.clientWidth * ratio));
    const height = Math.max(1, Math.round(canvas.clientHeight * ratio));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);

    pose.state.update(step);
    pose.state.apply(pose.skeleton);
    pose.skeleton.updateWorldTransform();
    this.keepClipsEnding(pose);
    this.aim(pose, width / height);
    renderer.begin();
    renderer.drawSkeleton(pose.skeleton, true);
    renderer.end();
  }

  /** 按取景对准镜头：贴着头顶，高度是身体的一段，宽度不窄于身体的约 70%，左右居中；全身时整个人居中。 */
  private aim({ bounds }: Pose, aspect: number): void {
    const camera = this.renderer.camera;
    if (this.framing === "full") {
      // 全身：整个人放进画面，四周留一点空，上下左右居中。
      const viewHeight = Math.max(bounds.height, bounds.width / aspect) * (1 + 2 * HEADROOM);
      camera.position.x = bounds.x + bounds.width / 2;
      camera.position.y = bounds.y + bounds.height / 2;
      camera.setViewport(viewHeight * aspect, viewHeight);
      this.view = { x: camera.position.x, y: camera.position.y, width: viewHeight * aspect, height: viewHeight };
      return;
    }
    const viewHeight = Math.max(bounds.height * FRAMING_HEIGHT[this.framing], (bounds.width * MIN_WIDTH) / aspect);
    const top = bounds.y + bounds.height * (1 + HEADROOM);
    camera.position.x = bounds.x + bounds.width / 2;
    camera.position.y = top - viewHeight / 2;
    camera.setViewport(viewHeight * aspect, viewHeight);
    this.view = { x: camera.position.x, y: camera.position.y, width: viewHeight * aspect, height: viewHeight };
  }

  /**
   * 绕开 spine-ts 3.6 的剪裁 bug（不改 runtime）：渲染器遇到没有附件的插槽直接跳过，也就跳过了
   * 「剪裁到此结束」的判断，结束插槽当下没有附件时剪裁永远不结束，后面画的东西全被裁掉
   * （2215 套里有 304 套在初始姿势下就会触发，例如「花風Smiley」的眼睛、睫毛、眉毛）。
   * 每帧把结束插槽改成「剪裁开始之后、原结束插槽为止，最后一个真的会画出来的插槽」；
   * 范围里这一帧什么都不画时，这一帧摘掉剪裁附件（动画挂回去了就再摘），有东西画了再挂回去。
   */
  private keepClipsEnding({ skeleton, clips }: Pose): void {
    const { ClippingAttachment, RegionAttachment, MeshAttachment } = this.spine;
    for (const slot of skeleton.slots) {
      const attachment = slot.getAttachment();
      if (attachment instanceof ClippingAttachment && attachment.endSlot && !clips.has(attachment)) {
        clips.set(attachment, { slot, end: attachment.endSlot, detached: false });
      }
    }
    const order = skeleton.drawOrder;
    for (const [clip, c] of clips) {
      const current = c.slot.getAttachment();
      if (current !== clip && !(c.detached && current === null)) {
        c.detached = false; // 动画给这个插槽换上了别的附件，不再归这里管
        continue;
      }
      const from = order.indexOf(c.slot);
      const to = order.findIndex((slot) => slot.data === c.end);
      if (from < 0 || to < 0) continue;
      let last: SlotData | null = null;
      for (let i = to; i > from; i--) {
        const drawn = order[i].getAttachment();
        if (drawn instanceof RegionAttachment || drawn instanceof MeshAttachment) {
          last = order[i].data;
          break;
        }
      }
      if (last) {
        clip.endSlot = last;
        if (c.detached) c.slot.setAttachment(clip);
        c.detached = false;
      } else {
        // 已经摘过也要再摘：有的衣装（例如「ザ・冬優子イズム」）的动画每帧都把剪裁附件挂回去，
        // 眨眼时范围里什么都不画，挂回去的剪裁就会把后面的眼皮、眉毛、手臂都裁掉。
        c.slot.setAttachment(null);
        c.detached = true;
      }
    }
  }
}

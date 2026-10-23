/**
 * 官方 spine-ts 3.6 的 WebGL runtime（spec「渲染」、ADR-0009）：`public/vendor/spine-3.6/spine-webgl.js`
 * 原样放着，一个字都不改，所以不能 import；页面在客户端用 `<script>` 加载它，经 `window.spine` 调用。
 * 下面的类型只描述立绘用到的那一小部分 3.6 API。
 */

/** runtime 在站点上的地址（换衣装的预取也用它）。 */
export const SPINE_RUNTIME_URL = "/vendor/spine-3.6/spine-webgl.js";

let loading: Promise<SpineRuntime> | null = null;

/**
 * 加载 runtime：整页只插一次 `<script>`，之后直接用已经加载好的。
 * 加载失败时忘掉这一次，下次打电话再试（spec「通话层」）。
 */
export function loadSpineRuntime(): Promise<SpineRuntime> {
  const loaded = (window as SpineWindow).spine;
  if (loaded) return Promise.resolve(loaded);
  loading ??= new Promise<SpineRuntime>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = SPINE_RUNTIME_URL;
    script.async = true;
    script.onload = () => {
      const spine = (window as SpineWindow).spine;
      if (spine) resolve(spine);
      else reject(new Error(`${SPINE_RUNTIME_URL} 没有提供 window.spine`));
    };
    script.onerror = () => {
      script.remove();
      reject(new Error(`加载不到 ${SPINE_RUNTIME_URL}`));
    };
    document.head.appendChild(script);
  }).catch((error: unknown) => {
    loading = null;
    throw error;
  });
  return loading;
}

type SpineWindow = Window & { spine?: SpineRuntime };

type Class<T, Args extends unknown[] = never[]> = new (...args: Args) => T;

export type SpineRuntime = {
  webgl: {
    ManagedWebGLRenderingContext: Class<ManagedWebGLRenderingContext, [HTMLCanvasElement, WebGLContextAttributes]>;
    SceneRenderer: Class<SceneRenderer, [HTMLCanvasElement, ManagedWebGLRenderingContext, boolean]>;
    AssetManager: Class<AssetManager, [ManagedWebGLRenderingContext]>;
  };
  AtlasAttachmentLoader: Class<AttachmentLoader, [TextureAtlas]>;
  SkeletonJson: Class<SkeletonJson, [AttachmentLoader]>;
  Skeleton: Class<Skeleton, [SkeletonData]>;
  AnimationStateData: Class<AnimationStateData, [SkeletonData]>;
  AnimationState: Class<AnimationState, [AnimationStateData]>;
  Vector2: Class<Vector2, []>;
  ClippingAttachment: Class<ClippingAttachment>;
  RegionAttachment: Class<Attachment>;
  MeshAttachment: Class<Attachment>;
  EventTimeline: Class<EventTimeline>;
};

export type ManagedWebGLRenderingContext = {
  /** 浏览器不支持 WebGL 时为 null。 */
  gl: WebGLRenderingContext | null;
};

export type SceneRenderer = {
  camera: { position: { x: number; y: number }; setViewport(width: number, height: number): void };
  begin(): void;
  drawSkeleton(skeleton: Skeleton, premultipliedAlpha: boolean): void;
  end(): void;
  dispose(): void;
};

export type AssetManager = {
  /** 载入的素材，按地址：文字是字符串，图集和贴图可以 dispose。 */
  assets: Record<string, string | { dispose?: () => void } | null>;
  loadText(path: string, success: () => void, error: (path: string, message: string) => void): void;
  loadTextureAtlas(path: string, success: () => void, error: (path: string, message: string) => void): void;
  get(path: string): unknown;
  dispose(): void;
};

export type TextureAtlas = { readonly __brand: "TextureAtlas" };
type AttachmentLoader = { readonly __brand: "AttachmentLoader" };

export type SkeletonJson = { readSkeletonData(json: string): SkeletonData };

export type SkeletonData = {
  animations: Animation[];
  findSkin(name: string): object | null;
  findAnimation(name: string): Animation | null;
};

export type Animation = { name: string; duration: number; timelines: object[] };

/** 动画里的事件，例如身体动作的 `loop_start`、`relay`。 */
export type EventTimeline = { events: SpineEvent[] };

export type SpineEvent = { time: number; stringValue: string | null; data: { name: string; stringValue: string | null } };

export type Attachment = { name: string };

export type ClippingAttachment = Attachment & {
  /** 剪裁到这个插槽为止；3.6 的渲染器在它当下没有附件时不会结束剪裁。 */
  endSlot: SlotData | null;
};

export type SlotData = { index: number };

export type Slot = {
  data: SlotData;
  getAttachment(): Attachment | null;
  setAttachment(attachment: Attachment | null): void;
};

/** 一根骨骼：名字和这一帧在世界坐标里的位置。 */
export type Bone = { data: { name: string }; worldX: number; worldY: number };

export type Skeleton = {
  slots: Slot[];
  bones: Bone[];
  findBone(name: string): Bone | null;
  drawOrder: Slot[];
  setSkinByName(name: string): void;
  setToSetupPose(): void;
  updateWorldTransform(): void;
  getBounds(offset: Vector2, size: Vector2, temp: number[]): void;
};

export type Vector2 = { x: number; y: number };

export type AnimationStateData = { defaultMix: number };

export type AnimationState = {
  setAnimation(track: number, name: string, loop: boolean): TrackEntry;
  addAnimation(track: number, name: string, loop: boolean, delay: number): TrackEntry;
  setEmptyAnimation(track: number, mixDuration: number): TrackEntry;
  addEmptyAnimation(track: number, mixDuration: number, delay: number): TrackEntry;
  getCurrent(track: number): TrackEntry | null;
  update(delta: number): void;
  apply(skeleton: Skeleton): boolean;
};

/** 一条轨道上正在播或排着的动画。条目会被 runtime 回收再用，不要长期持有。 */
export type TrackEntry = {
  animation: Animation;
  loop: boolean;
  trackTime: number;
  animationStart: number;
  animationEnd: number;
  mixDuration: number;
  listener: { complete?: (entry: TrackEntry) => void } | null;
};

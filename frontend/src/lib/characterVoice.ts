// Playing the character's voice in a call, plain, over a phone line (GLOSSARY.md「电话质感」), or
// close to the ear in an ASMR call (ASMR 模式).
//
// Plain, the page's <audio> element plays her track straight, as it always has. With phone
// texture on, her track goes through Web Audio instead: the phone band, a light compressor,
// then a gain that text only turns down to nothing. In an ASMR call it goes through the ear
// instead (`nearEar`), the phone line or not: an ASMR call is never over a phone line. The element still holds the stream,
// muted: Chrome gives no sound from a remote WebRTC stream that no media element holds.
// Only her voice goes this way; the call tones play on their own.
//
// A call she took in the tub (GLOSSARY.md「洗澡」) puts her voice in a bathroom: a short, bright
// tiled-room reverb before the line, however it is played, and under it the bathroom's own
// sound (`public/ambience/bath-1.mp3`, if it is there; without it, only the reverb).

import { Ambience, type AmbienceKind } from "@/lib/ambience";

/** The phone band: roughly what a phone line carries, in Hz. */
const PHONE_BAND = { low: 300, high: 3400 } as const;

/** Builds the phone line after `input`: band limited, lightly compressed. Returns the last node. */
function phoneLine(context: BaseAudioContext, input: AudioNode): AudioNode {
  // Two filters at each edge (24 dB per octave), so the band sounds narrow and not merely dulled.
  const edges: [BiquadFilterType, number][] = [
    ["highpass", PHONE_BAND.low],
    ["highpass", PHONE_BAND.low],
    ["lowpass", PHONE_BAND.high],
    ["lowpass", PHONE_BAND.high],
  ];
  let last = input;
  for (const [type, frequency] of edges) {
    const filter = context.createBiquadFilter();
    filter.type = type;
    filter.frequency.value = frequency;
    filter.Q.value = Math.SQRT1_2;
    last = last.connect(filter);
  }
  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = -24;
  compressor.knee.value = 12;
  compressor.ratio.value = 3;
  compressor.attack.value = 0.005;
  compressor.release.value = 0.2;
  return last.connect(compressor);
}

/** Her voice close to the ear: as a microphone picks up a voice an inch away. */
export const NEAR_EAR = {
  /** The proximity effect: the warmth of a voice right at the microphone. */
  warmth: { frequency: 180, gain: 6 },
  /** The breath and the lips, which a whisper is made of. */
  air: { frequency: 6000, gain: 4 },
  /** Brings the quiet of a whisper up close, and its makeup gain after it. */
  compressor: { threshold: -32, knee: 10, ratio: 4, attack: 0.003, release: 0.25 },
  makeup: 1.6,
  /** Her voice drifting from one ear to the other and back: how often (Hz; once in 10 seconds), how far. */
  drift: { frequency: 0.1, depth: 0.7 },
} as const;

/** Builds the ear after `input`: warm, breathy, brought close, drifting from ear to ear.
 * Returns the last node, and the drift, to stop with the line. */
function nearEar(context: BaseAudioContext, input: AudioNode): { out: AudioNode; drift: OscillatorNode } {
  const warmth = context.createBiquadFilter();
  warmth.type = "lowshelf";
  warmth.frequency.value = NEAR_EAR.warmth.frequency;
  warmth.gain.value = NEAR_EAR.warmth.gain;
  const air = context.createBiquadFilter();
  air.type = "highshelf";
  air.frequency.value = NEAR_EAR.air.frequency;
  air.gain.value = NEAR_EAR.air.gain;
  const compressor = context.createDynamicsCompressor();
  compressor.threshold.value = NEAR_EAR.compressor.threshold;
  compressor.knee.value = NEAR_EAR.compressor.knee;
  compressor.ratio.value = NEAR_EAR.compressor.ratio;
  compressor.attack.value = NEAR_EAR.compressor.attack;
  compressor.release.value = NEAR_EAR.compressor.release;
  const makeup = context.createGain();
  makeup.gain.value = NEAR_EAR.makeup;
  const panner = context.createStereoPanner();
  const drift = context.createOscillator();
  drift.type = "sine";
  drift.frequency.value = NEAR_EAR.drift.frequency;
  const depth = context.createGain();
  depth.gain.value = NEAR_EAR.drift.depth;
  drift.connect(depth).connect(panner.pan);
  drift.start();
  const out = input.connect(warmth).connect(air).connect(compressor).connect(makeup).connect(panner);
  return { out, drift };
}

/** Her voice in the bathroom: how long the tiles ring (seconds), how fast it dies away, how
 * much of the ring is heard beside her voice; and the bathroom's own sound under it. */
export const BATHROOM = {
  seconds: 1.4,
  decay: 3,
  wet: 0.35,
  ambience: "/ambience/bath-1.mp3",
  ambienceGain: 0.18,
  fade: 1.5,
} as const;

/** The ring of a small tiled room: noise dying away, a little different in each ear.
 * `random` is 0 to 1. */
export function bathroomImpulse(
  context: BaseAudioContext,
  random: () => number = Math.random,
): AudioBuffer {
  const length = Math.floor(context.sampleRate * BATHROOM.seconds);
  const impulse = context.createBuffer(2, length, context.sampleRate);
  for (let channel = 0; channel < 2; channel++) {
    const data = impulse.getChannelData(channel);
    for (let i = 0; i < length; i++) data[i] = (random() * 2 - 1) * (1 - i / length) ** BATHROOM.decay;
  }
  return impulse;
}

/** Builds the bathroom after `input`: her voice as it is, with the room's ring beside it.
 * Returns the last node. */
function bathroom(context: BaseAudioContext, input: AudioNode): AudioNode {
  const out = context.createGain();
  const ring = context.createConvolver();
  ring.buffer = bathroomImpulse(context);
  const wet = context.createGain();
  wet.gain.value = BATHROOM.wet;
  input.connect(out);
  input.connect(ring).connect(wet).connect(out);
  return out;
}

/** How her voice is played in a call. */
type Playing = "plain" | "phone" | "ear";

export class CharacterVoice {
  private element: HTMLAudioElement | null = null;
  private context: AudioContext | null = null;
  /** How this call is played; decided when dialing, fixed for the call. */
  private playing: Playing = "plain";
  private textOnly = false;
  /** The line her current track plays through, over the phone or by the ear. */
  private line: { source: AudioNode; volume: GainNode; drift?: OscillatorNode } | null = null;
  /** Her track in the call under way, to build the line again from. */
  private stream: MediaStream | null = null;
  /** She took this call in the tub (GLOSSARY.md「洗澡」). */
  private inBath = false;
  /** The bathroom's own sound, while it plays. */
  private bathSound: { source: AudioBufferSourceNode; gain: GainNode } | null = null;

  /** The ambience played under her voice (ASMR 模式), on the same audio context. */
  readonly ambience: Ambience;

  constructor(private readonly createContext: () => AudioContext = () => new AudioContext()) {
    this.ambience = new Ambience(() => this.audioContext());
  }

  private audioContext(): AudioContext {
    this.context ??= this.createContext();
    if (this.context.state === "suspended") void this.context.resume().catch(() => {});
    return this.context;
  }

  /** The page's <audio> element (null once it is gone); usable as its ref. */
  readonly attach = (element: HTMLAudioElement | null) => {
    this.element = element;
    this.applyMuting();
  };

  /** Called while the user dials, inside their click: the audio context may only start from a
   * user gesture, else autoplay rules keep it suspended. An ASMR call is by the ear, never
   * over the phone line. */
  dial(phoneTexture: boolean, asmr = false) {
    this.playing = asmr ? "ear" : phoneTexture ? "phone" : "plain";
    this.disconnectLine();
    this.stream = null;
    this.inBath = false;
    this.stopBathSound();
    if (this.playing !== "plain") this.audioContext();
    this.ambience.callStarted(asmr);
    this.applyMuting();
  }

  /** She starts speaking in the call, or stops: the ambience under her goes down, and back. */
  speaking(speaking: boolean) {
    this.ambience.duck(speaking);
  }

  /** The call is over: the ambience fades out. */
  callEnded() {
    this.ambience.callEnded();
    this.stopBathSound();
  }

  /** She took the call in the tub (GLOSSARY.md「洗澡」): her voice goes through the bathroom
   * from now on, and the bathroom's own sound plays under it. */
  bath() {
    if (this.inBath) return;
    this.inBath = true;
    const context = this.audioContext();
    if (this.stream) this.connectLine(this.stream);
    this.applyMuting();
    void this.startBathSound(context);
  }

  /** The call has connected: the ambience can now ask the backend to pick it. */
  callConnected(setAmbienceAuto: (on: boolean) => void) {
    this.ambience.connected(setAmbienceAuto);
  }

  /** The backend picked the ambience. */
  ambiencePicked(kind: AmbienceKind) {
    this.ambience.picked(kind);
  }

  /** Her voice has arrived for this call. */
  play(track: MediaStreamTrack) {
    const stream = new MediaStream([track]);
    this.stream = stream;
    if (this.element) this.element.srcObject = stream;
    this.connectLine(stream);
    this.applyMuting();
  }

  /** Her track through Web Audio, if it is played other than plain or she is in the bath:
   * the bathroom first, then the phone line or the ear. */
  private connectLine(stream: MediaStream) {
    this.disconnectLine();
    if ((this.playing === "plain" && !this.inBath) || !this.context) return;
    const source = this.context.createMediaStreamSource(stream);
    const room = this.inBath ? bathroom(this.context, source) : source;
    const volume = this.context.createGain();
    if (this.playing === "ear") {
      const { out, drift } = nearEar(this.context, room);
      out.connect(volume).connect(this.context.destination);
      this.line = { source, volume, drift };
    } else {
      const out = this.playing === "phone" ? phoneLine(this.context, room) : room;
      out.connect(volume).connect(this.context.destination);
      this.line = { source, volume };
    }
  }

  private async startBathSound(context: AudioContext) {
    let buffer: AudioBuffer;
    try {
      const res = await fetch(BATHROOM.ambience);
      if (!res.ok) return;
      buffer = await context.decodeAudioData(await res.arrayBuffer());
    } catch {
      return; // 没放环境音文件，或者解不开：只有回响
    }
    if (!this.inBath || this.bathSound) return;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.loop = true;
    const gain = context.createGain();
    gain.gain.setValueAtTime(0, context.currentTime);
    gain.gain.linearRampToValueAtTime(BATHROOM.ambienceGain, context.currentTime + BATHROOM.fade);
    source.connect(gain).connect(context.destination);
    source.start();
    this.bathSound = { source, gain };
  }

  private stopBathSound() {
    const playing = this.bathSound;
    this.bathSound = null;
    if (!playing || !this.context) return;
    const now = this.context.currentTime;
    playing.gain.gain.cancelScheduledValues(now);
    playing.gain.gain.setValueAtTime(playing.gain.gain.value, now);
    playing.gain.gain.linearRampToValueAtTime(0, now + BATHROOM.fade);
    playing.source.stop(now + BATHROOM.fade);
  }

  /** GLOSSARY.md「只看文字」: her voice is silent, over the phone line or not. */
  setTextOnly(textOnly: boolean) {
    this.textOnly = textOnly;
    this.applyMuting();
  }

  dispose() {
    this.ambience.dispose();
    this.stopBathSound();
    this.disconnectLine();
    void this.context?.close().catch(() => {});
    this.context = null;
  }

  private applyMuting() {
    if (this.element) this.element.muted = this.playing !== "plain" || this.inBath || this.textOnly;
    if (this.line) this.line.volume.gain.value = this.textOnly ? 0 : 1;
    if (this.bathSound) this.bathSound.gain.gain.value = this.textOnly ? 0 : BATHROOM.ambienceGain;
  }

  private disconnectLine() {
    this.line?.source.disconnect();
    this.line?.drift?.stop();
    this.line = null;
  }
}

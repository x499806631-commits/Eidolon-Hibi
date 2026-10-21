// Playing the character's voice in a call, plain, over a phone line (GLOSSARY.md「电话质感」), or
// close to the ear in an ASMR call (ASMR 模式).
//
// Plain, the page's <audio> element plays her track straight, as it always has. With phone
// texture on, her track goes through Web Audio instead: the phone band, a light compressor,
// then a gain that text only turns down to nothing. In an ASMR call it goes through the ear
// instead (`nearEar`), the phone line or not: an ASMR call is never over a phone line. The element still holds the stream,
// muted: Chrome gives no sound from a remote WebRTC stream that no media element holds.
// Only her voice goes this way; the call tones play on their own.

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
    if (this.element) this.element.srcObject = stream;
    this.disconnectLine();
    if (this.playing !== "plain" && this.context) {
      const source = this.context.createMediaStreamSource(stream);
      const volume = this.context.createGain();
      if (this.playing === "ear") {
        const { out, drift } = nearEar(this.context, source);
        out.connect(volume).connect(this.context.destination);
        this.line = { source, volume, drift };
      } else {
        phoneLine(this.context, source).connect(volume).connect(this.context.destination);
        this.line = { source, volume };
      }
    }
    this.applyMuting();
  }

  /** GLOSSARY.md「只看文字」: her voice is silent, over the phone line or not. */
  setTextOnly(textOnly: boolean) {
    this.textOnly = textOnly;
    this.applyMuting();
  }

  dispose() {
    this.ambience.dispose();
    this.disconnectLine();
    void this.context?.close().catch(() => {});
    this.context = null;
  }

  private applyMuting() {
    if (this.element) this.element.muted = this.playing !== "plain" || this.textOnly;
    if (this.line) this.line.volume.gain.value = this.textOnly ? 0 : 1;
  }

  private disconnectLine() {
    this.line?.source.disconnect();
    this.line?.drift?.stop();
    this.line = null;
  }
}

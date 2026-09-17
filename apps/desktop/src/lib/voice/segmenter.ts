/**
 * Turns a stream of loudness readings into utterances — the part of hands-free voice that can be
 * tested without a microphone.
 *
 * One reading per audio frame (root-mean-square of the samples). Speech begins when a few frames in
 * a row rise clearly above the noise floor, and ends after enough silence; the utterance handed
 * back is the audio from a little BEFORE the first loud frame (the pre-roll, so the first syllable
 * is not clipped by the reaction time) to the end of the hangover. Utterances shorter than a word
 * are discarded rather than transcribed — a cough is not a message.
 *
 * The floor is not a constant. Rooms differ, microphones differ, and the loudest thing in the room
 * while the agent reads an answer aloud is the agent. So the floor is tracked from the readings
 * themselves, and never from a loud run — a person talking must not raise it under themselves.
 * While listening it drifts toward the quiet readings slowly. When the agent starts to speak, the
 * first `calibrationMs` of readings feed it quickly with no events allowed, so the agent's own
 * voice through the speakers becomes the baseline; from then on a person interrupting has to be
 * `bargeInFactor` times louder than that. That is the barge-in rule, and it is a heuristic with two
 * numbers in it — stated here so it can be measured against a real room rather than believed.
 */

export interface SegmenterOptions {
  /** Milliseconds each reading covers. */
  frameMs: number;
  /** A frame counts as loud above `floor × startFactor`; while the agent speaks, `bargeInFactor`. */
  startFactor: number;
  bargeInFactor: number;
  /** Below this absolute RMS nothing is ever speech — the floor of a silent, well-behaved mic. */
  minRms: number;
  /** Loud frames in a row before speech begins. */
  startFrames: number;
  /** Silence after speech before the utterance ends. */
  hangoverMs: number;
  /** Utterances shorter than this (loud part) are discarded. */
  minSpeechMs: number;
  /** Audio kept from before the first loud frame. */
  prerollMs: number;
  /** An utterance is cut here whatever the loudness — a long monologue still reaches the agent. */
  maxUtteranceMs: number;
  /** When the agent starts reading aloud: how long the floor is let climb to its voice, with no
   *  events — the speakers' level as the mic hears it is measured before anything can interrupt. */
  calibrationMs: number;
}

export const DEFAULT_SEGMENTER: SegmenterOptions = {
  frameMs: 64,
  startFactor: 3,
  bargeInFactor: 4,
  minRms: 0.008,
  startFrames: 3,
  hangoverMs: 700,
  minSpeechMs: 350,
  prerollMs: 320,
  maxUtteranceMs: 30_000,
  calibrationMs: 800,
};

export type SegmentEvent =
  | { kind: "start" }
  | { kind: "end"; samples: Float32Array; speechMs: number }
  | { kind: "discard"; speechMs: number };

export class Segmenter {
  readonly options: SegmenterOptions;
  /** The tracked noise floor, as RMS. Public so a screen can show it and a test can pin it. */
  floor: number;
  private speakingNow = false;
  private calibrating = 0;

  /** Whether the agent is reading aloud right now — changes the floor's rule and the threshold. */
  get agentSpeaking(): boolean {
    return this.speakingNow;
  }

  set agentSpeaking(on: boolean) {
    if (on && !this.speakingNow) {
      // The speakers have just started: let the floor climb to them, and count nothing as an
      // interruption until it has. A candidate run that had begun is dropped with it.
      this.calibrating = Math.ceil(this.options.calibrationMs / this.options.frameMs);
      this.loudRun = 0;
    }
    if (!on) this.calibrating = 0;
    this.speakingNow = on;
  }

  private loudRun = 0;
  private quietMs = 0;
  private speechMs = 0;
  private inSpeech = false;
  private preroll: Float32Array[] = [];
  private prerollMs = 0;
  private utterance: Float32Array[] = [];
  private utteranceMs = 0;

  constructor(options: Partial<SegmenterOptions> = {}) {
    this.options = { ...DEFAULT_SEGMENTER, ...options };
    this.floor = this.options.minRms;
  }

  /** The loudness a frame must reach to count as speech right now. */
  get threshold(): number {
    const factor = this.agentSpeaking ? this.options.bargeInFactor : this.options.startFactor;
    return Math.max(this.options.minRms, this.floor * factor);
  }

  /** Feed one frame. Returns an event when an utterance begins or ends, else null. */
  feed(rms: number, samples: Float32Array): SegmentEvent | null {
    const { frameMs } = this.options;
    if (this.speakingNow && this.calibrating > 0 && !this.inSpeech) {
      // Calibrating to the agent's own voice: every frame feeds the floor, quickly, and none is an
      // event. Pre-roll keeps rolling so a barge-in right after calibration is not clipped.
      this.calibrating -= 1;
      this.floor = Math.max(this.options.minRms, this.floor + (rms - this.floor) * 0.3);
      this.pushPreroll(samples);
      return null;
    }
    const loud = rms >= this.threshold;
    this.trackFloor(rms, loud);

    if (!this.inSpeech) {
      this.pushPreroll(samples);
      if (loud) {
        this.loudRun += 1;
        if (this.loudRun >= this.options.startFrames) {
          this.inSpeech = true;
          this.loudRun = 0;
          this.quietMs = 0;
          this.speechMs = this.options.startFrames * frameMs;
          this.utterance = [...this.preroll];
          this.utteranceMs = this.prerollMs;
          return { kind: "start" };
        }
      } else {
        this.loudRun = 0;
      }
      return null;
    }

    this.utterance.push(samples);
    this.utteranceMs += frameMs;
    if (loud) {
      this.quietMs = 0;
      this.speechMs += frameMs;
    } else {
      this.quietMs += frameMs;
    }
    const ended = this.quietMs >= this.options.hangoverMs;
    const cut = this.utteranceMs >= this.options.maxUtteranceMs;
    if (!ended && !cut) return null;
    return this.finish();
  }

  /** End the current utterance now (the mode is being switched off). Null when there is none. */
  flush(): SegmentEvent | null {
    return this.inSpeech ? this.finish() : null;
  }

  private finish(): SegmentEvent {
    const speechMs = this.speechMs;
    const frames = this.utterance;
    this.inSpeech = false;
    this.utterance = [];
    this.utteranceMs = 0;
    this.speechMs = 0;
    this.quietMs = 0;
    this.preroll = [];
    this.prerollMs = 0;
    if (speechMs < this.options.minSpeechMs) return { kind: "discard", speechMs };
    return { kind: "end", samples: concat(frames), speechMs };
  }

  private pushPreroll(samples: Float32Array): void {
    this.preroll.push(samples);
    this.prerollMs += this.options.frameMs;
    while (this.prerollMs > this.options.prerollMs && this.preroll.length > 1) {
      this.preroll.shift();
      this.prerollMs -= this.options.frameMs;
    }
  }

  private trackFloor(rms: number, loud: boolean): void {
    // Never toward a loud frame: a person talking must not raise the floor under themselves, and
    // that is as true of a person interrupting the agent as of one starting cold. Between loud
    // runs the floor follows the room — slowly upward while listening, faster in both directions
    // while the agent reads, because its voice through the speakers is the baseline then (the
    // first `calibrationMs` of it are measured before any event is possible; see `feed`).
    if (loud) return;
    const alpha = this.speakingNow ? 0.1 : rms < this.floor ? 0.2 : 0.02;
    this.floor = Math.max(this.options.minRms, this.floor + (rms - this.floor) * alpha);
  }
}

/** Root-mean-square of a frame — the loudness reading the segmenter consumes. */
export function rmsOf(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

function concat(frames: Float32Array[]): Float32Array {
  const total = frames.reduce((n, f) => n + f.length, 0);
  const out = new Float32Array(total);
  let at = 0;
  for (const f of frames) {
    out.set(f, at);
    at += f.length;
  }
  return out;
}

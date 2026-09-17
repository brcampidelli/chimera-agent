/**
 * The microphone as a stream of 64 ms frames of samples, at 16 kHz mono.
 *
 * Raw samples rather than `MediaRecorder`, because the voice mode cuts utterances out of a
 * continuous capture and needs the audio from BEFORE it decided speech had started (the pre-roll)
 * — a recorder started on that decision clips the first syllable. The frames go to the segmenter
 * as they come; what it hands back is encoded to WAV and sent.
 *
 * Echo cancellation is asked for. What the WebView's implementation cancels — the browser's own
 * output, the system's — differs by platform and is not promised here; the segmenter's floor
 * tracking is the defence that does not depend on it (see `Segmenter`).
 *
 * `ScriptProcessorNode` is deprecated in favour of `AudioWorklet` and still present in every
 * Chromium the desktop ships with; the worklet needs a separately served module, which the
 * desktop's asset protocol makes a project of its own. The frame size is 1024 samples: 64 ms at
 * 16 kHz, fine enough for a 700 ms hangover and a barge-in that feels immediate.
 */

export const SAMPLE_RATE = 16_000;
export const FRAME_SAMPLES = 1024;
export const FRAME_MS = Math.round((FRAME_SAMPLES / SAMPLE_RATE) * 1000);

export type FrameSink = (samples: Float32Array) => void;

export interface MicrophoneLike {
  /** Open the mic and start delivering frames. Rejects when there is no mic or no permission. */
  start(sink: FrameSink): Promise<void>;
  /** Release the mic. Safe to call twice. */
  stop(): void;
  readonly sampleRate: number;
}

export class BrowserMicrophone implements MicrophoneLike {
  readonly sampleRate = SAMPLE_RATE;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private processor: ScriptProcessorNode | null = null;

  async start(sink: FrameSink): Promise<void> {
    this.stop();
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const context = new AudioContext({ sampleRate: SAMPLE_RATE });
    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(FRAME_SAMPLES, 1, 1);
    processor.onaudioprocess = (event) => {
      // Copied: the buffer is reused by the audio thread and the segmenter keeps frames.
      sink(new Float32Array(event.inputBuffer.getChannelData(0)));
    };
    source.connect(processor);
    // A ScriptProcessor only runs while connected to the destination; the gain node keeps its
    // output silent so the mic is never played back.
    const mute = context.createGain();
    mute.gain.value = 0;
    processor.connect(mute);
    mute.connect(context.destination);
    this.stream = stream;
    this.context = context;
    this.processor = processor;
    if (context.state === "suspended") await context.resume();
  }

  stop(): void {
    if (this.processor) {
      this.processor.onaudioprocess = null;
      this.processor.disconnect();
      this.processor = null;
    }
    if (this.stream) {
      // Release the microphone the moment it is not needed: an open mic is a recording indicator
      // nobody can explain.
      for (const track of this.stream.getTracks()) track.stop();
      this.stream = null;
    }
    if (this.context) {
      void this.context.close().catch(() => undefined);
      this.context = null;
    }
  }
}

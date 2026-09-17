/**
 * An utterance as a WAV file: 16-bit PCM, mono, at the capture rate.
 *
 * WAV rather than the `MediaRecorder` WebM the dictation button uploads, because an utterance is
 * cut out of a continuous capture and a slice of a WebM stream is not a file — the container's
 * header is at the front and the slice has none. PCM has no such problem: samples in, samples
 * out, and the 44-byte header is written here. Sixteen kilohertz mono is what every speech model
 * this app can reach wants anyway, so nothing is lost that a transcriber would have used.
 */

export function encodeWav(samples: Float32Array, sampleRate: number): Blob {
  return new Blob([encodeWavBytes(samples, sampleRate)], { type: "audio/wav" });
}

/** The file's bytes — `encodeWav` without the Blob, for a reader that wants to look inside. */
export function encodeWavBytes(samples: Float32Array, sampleRate: number): ArrayBuffer {
  const bytesPerSample = 2;
  const buffer = new ArrayBuffer(44 + samples.length * bytesPerSample);
  const view = new DataView(buffer);
  const write = (at: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(at + i, text.charCodeAt(i));
  };
  write(0, "RIFF");
  view.setUint32(4, 36 + samples.length * bytesPerSample, true);
  write(8, "WAVE");
  write(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true);
  view.setUint16(32, bytesPerSample, true);
  view.setUint16(34, 16, true); // bits per sample
  write(36, "data");
  view.setUint32(40, samples.length * bytesPerSample, true);
  let at = 44;
  for (let i = 0; i < samples.length; i++) {
    // Clamp, then scale: a sample above ±1 (a clipped mic) must not wrap around into noise.
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(at, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    at += bytesPerSample;
  }
  return buffer;
}

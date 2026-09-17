import { describe, expect, it } from "vitest";

import { encodeWav, encodeWavBytes } from "@/lib/voice/wav";

describe("encodeWav", () => {
  it("writes a 44-byte PCM header the transcriber can read, and the samples as 16-bit", async () => {
    const samples = new Float32Array([0, 0.5, -0.5, 1, -1, 2, -2]);
    const blob = encodeWav(samples, 16_000);
    expect(blob.type).toBe("audio/wav");
    expect(blob.size).toBe(44 + samples.length * 2);
    const bytes = new DataView(encodeWavBytes(samples, 16_000));
    const ascii = (at: number, n: number) =>
      Array.from({ length: n }, (_, i) => String.fromCharCode(bytes.getUint8(at + i))).join("");
    expect(ascii(0, 4)).toBe("RIFF");
    expect(ascii(8, 4)).toBe("WAVE");
    expect(ascii(12, 4)).toBe("fmt ");
    expect(bytes.getUint16(20, true)).toBe(1); // PCM
    expect(bytes.getUint16(22, true)).toBe(1); // mono
    expect(bytes.getUint32(24, true)).toBe(16_000);
    expect(bytes.getUint32(28, true)).toBe(32_000); // bytes per second
    expect(bytes.getUint16(34, true)).toBe(16);
    expect(ascii(36, 4)).toBe("data");
    expect(bytes.getUint32(40, true)).toBe(samples.length * 2);
    expect(bytes.byteLength).toBe(44 + samples.length * 2);
    const pcm = Array.from({ length: samples.length }, (_, i) => bytes.getInt16(44 + i * 2, true));
    expect(pcm[0]).toBe(0);
    expect(pcm[1]).toBe(Math.trunc(0.5 * 0x7fff));
    expect(pcm[2]).toBe(-0x4000);
    expect(pcm[3]).toBe(0x7fff);
    expect(pcm[4]).toBe(-0x8000);
    // Clipped input clamps rather than wrapping into noise.
    expect(pcm[5]).toBe(0x7fff);
    expect(pcm[6]).toBe(-0x8000);
  });
});

import { describe, expect, it } from "vitest";

import { DEFAULT_SEGMENTER, Segmenter, rmsOf, type SegmentEvent } from "@/lib/voice/segmenter";

/**
 * The segmenter, driven with loudness readings instead of a microphone.
 *
 * Every number here is a level, not a sound: quiet frames sit near the floor, loud ones well above
 * it. What is pinned is the shape of the machine — when an utterance starts, when it ends, what it
 * keeps, what it throws away — and the one rule that makes barge-in possible: while the agent
 * reads aloud, the floor climbs to the agent's own voice and only speech above THAT counts.
 */

const FRAME = DEFAULT_SEGMENTER.frameMs;

function frame(level: number, tag = 0): Float32Array {
  // Four samples at `level`; the first carries `tag` so a test can see which frames were kept.
  const f = new Float32Array(4).fill(level);
  f[0] = tag;
  return f;
}

function feed(seg: Segmenter, levels: number[], tagFrom = 0): SegmentEvent[] {
  const events: SegmentEvent[] = [];
  levels.forEach((level, i) => {
    const e = seg.feed(level, frame(level, tagFrom + i + 1));
    if (e) events.push(e);
  });
  return events;
}

const quiet = (n: number) => Array<number>(n).fill(0.003);
const loud = (n: number) => Array<number>(n).fill(0.2);
const HANGOVER_FRAMES = Math.ceil(DEFAULT_SEGMENTER.hangoverMs / FRAME);

describe("an utterance", () => {
  it("starts after a few loud frames in a row and ends after the hangover of silence", () => {
    const seg = new Segmenter();
    const events = feed(seg, [...quiet(10), ...loud(12), ...quiet(HANGOVER_FRAMES + 1)]);
    expect(events.map((e) => e.kind)).toEqual(["start", "end"]);
    const end = events[1] as Extract<SegmentEvent, { kind: "end" }>;
    expect(end.speechMs).toBe(12 * FRAME);
  });

  it("keeps the pre-roll: audio from before the frames that decided speech had started", () => {
    const seg = new Segmenter();
    const events = feed(seg, [...quiet(10), ...loud(12), ...quiet(HANGOVER_FRAMES + 1)]);
    const end = events[1] as Extract<SegmentEvent, { kind: "end" }>;
    // Frames are tagged 1..N in feed order; the utterance must begin BEFORE frame 11 (first loud).
    const firstKept = end.samples[0];
    expect(firstKept).toBeLessThan(11);
    // And no earlier than the pre-roll allows.
    const prerollFrames = Math.ceil(DEFAULT_SEGMENTER.prerollMs / FRAME);
    expect(firstKept).toBeGreaterThanOrEqual(11 - prerollFrames - DEFAULT_SEGMENTER.startFrames);
    // The last kept frame is inside the hangover, not the frame that ended it.
    expect(end.samples[end.samples.length - 4]).toBeGreaterThan(22);
  });

  it("is discarded when the loud part is shorter than a word", () => {
    const seg = new Segmenter();
    const events = feed(seg, [...quiet(10), ...loud(3), ...quiet(HANGOVER_FRAMES + 1)]);
    expect(events.map((e) => e.kind)).toEqual(["start", "discard"]);
  });

  it("does not start on a single loud frame — a click is not speech", () => {
    const seg = new Segmenter();
    const events = feed(seg, [...quiet(10), 0.2, ...quiet(10), 0.2, 0.2, ...quiet(10)]);
    expect(events).toEqual([]);
  });

  it("is cut at the maximum length even while the person keeps talking", () => {
    const seg = new Segmenter({ maxUtteranceMs: 1000 });
    const events = feed(seg, [...quiet(5), ...loud(40)]);
    // Cut, restarted, cut again: a monologue reaches the agent in pieces, none longer than the cap.
    expect(events.map((e) => e.kind).slice(0, 4)).toEqual(["start", "end", "start", "end"]);
    const first = events[1] as Extract<SegmentEvent, { kind: "end" }>;
    expect(first.samples.length / 4).toBeLessThanOrEqual(Math.ceil(1000 / FRAME) + Math.ceil(DEFAULT_SEGMENTER.prerollMs / FRAME) + DEFAULT_SEGMENTER.startFrames);
  });

  it("can be flushed when the mode is switched off", () => {
    const seg = new Segmenter();
    feed(seg, [...quiet(5), ...loud(12)]);
    const flushed = seg.flush();
    expect(flushed?.kind).toBe("end");
    expect(seg.flush()).toBeNull();
  });
});

describe("the floor", () => {
  it("rises to the room's noise while listening, so the threshold follows the room", () => {
    const seg = new Segmenter();
    const before = seg.threshold;
    feed(seg, Array<number>(60).fill(0.02));
    expect(seg.floor).toBeGreaterThan(0.015);
    expect(seg.threshold).toBeGreaterThan(before);
    // A person is still louder than the room.
    const events = feed(seg, [...loud(12), ...quiet(HANGOVER_FRAMES + 1)]);
    expect(events.map((e) => e.kind)).toEqual(["start", "end"]);
  });

  it("does not climb under a person who is talking", () => {
    const seg = new Segmenter();
    feed(seg, quiet(10));
    const floorBefore = seg.floor;
    feed(seg, loud(20));
    expect(seg.floor).toBe(floorBefore);
  });

  it("climbs to the agent's own voice while it reads aloud, so only louder speech interrupts", () => {
    const seg = new Segmenter();
    feed(seg, quiet(10));
    seg.agentSpeaking = true;
    // The agent's voice through the speakers, as the mic hears it: steady, moderate.
    const agentVoice = 0.05;
    const heardWhileSpeaking = feed(seg, Array<number>(40).fill(agentVoice));
    // Within a couple of seconds the floor is at the agent's level and the threshold above it.
    expect(seg.floor).toBeGreaterThan(agentVoice * 0.9);
    expect(seg.threshold).toBeGreaterThan(agentVoice * 3);
    // The agent's own voice, continuing, is NOT an utterance.
    const more = feed(seg, Array<number>(20).fill(agentVoice));
    expect([...heardWhileSpeaking, ...more].filter((e) => e.kind === "start").length).toBeLessThanOrEqual(1);
    // A person talking over it, louder than the speakers, is.
    const bargeIn = feed(seg, Array<number>(6).fill(0.4));
    expect(bargeIn.some((e) => e.kind === "start")).toBe(true);
  });
});

describe("rmsOf", () => {
  it("is the root mean square, and zero for nothing", () => {
    expect(rmsOf(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5, 6);
    expect(rmsOf(new Float32Array(0))).toBe(0);
  });
});

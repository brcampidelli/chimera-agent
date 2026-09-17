/**
 * Server-sent events off a `fetch` body — the one reader every stream in this app goes through.
 *
 * `fetch` rather than `EventSource`, on purpose: `EventSource` cannot send an `Authorization`
 * header, and every stream here is behind a bearer token when the app is pointed at a remote
 * server (or, for a guest, always). A stream is read frame by frame — a blank line ends a frame —
 * with CRLF folded to LF, because a proxy between the two can rewrite line endings and a reader
 * that trusts `\n\n` alone stops seeing frames the moment one does.
 */

export interface SseFrame {
  event: string;
  data: string;
}

/** Read every frame of `body`, calling `onFrame` with its raw text. Resolves with null when the
 *  stream ends cleanly, or with the error's message when it was cut. */
export async function readSseFrames(
  body: ReadableStream<Uint8Array>,
  onFrame: (frame: string) => void,
): Promise<string | null> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer = (buffer + decoder.decode(value, { stream: true })).replace(/\r\n/g, "\n");
      let sep: number;
      while ((sep = buffer.indexOf("\n\n")) !== -1) {
        onFrame(buffer.slice(0, sep));
        buffer = buffer.slice(sep + 2);
      }
    }
  } catch (err) {
    return err instanceof Error ? err.message : String(err);
  }
  if (buffer.trim()) onFrame(buffer);
  return null;
}

/** The `event:` and `data:` lines of one raw frame. A frame with no event is `message`, as the
 *  spec says; `data` lines are joined with newlines, also as the spec says. */
export function parseSseFrame(frame: string): SseFrame {
  let event = "message";
  const data: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  return { event, data: data.join("\n") };
}

# Profiling the conversation

The render-count tests (`Conversation.stream.test.tsx`) prove the mechanism: finished exchanges are
not re-rendered while one streams, and tokens are written once per frame. They do not prove the
screen is smoother. That needs a measurement in the packaged WebView2, which a jsdom test cannot give.

## Script

1. Build the packaged app (`npm run tauri build`) and open it. Use the same machine and the same
   power plan for every run; close other windows.
2. Open a conversation recorded with **200 exchanges** (a saved replay: the same transcript every
   time, so the only variable is the build under test).
3. Open WebView2 DevTools, **Performance** tab. Leave CPU throttling off.
4. Start recording, then replay a turn that streams a long answer (about 2,000 tokens) into the
   last exchange. Stop recording when the answer is complete.
5. Repeat **3 times** per build. Compare the build before this change (`main`) with the one after.

## What to read

- **React Profiler** (React DevTools, "Record why each component rendered"): how many `ExchangeView`
  commits happen per token. Expected after: only the last exchange.
- **Long tasks** (Performance tab, tasks over 50 ms): count and total duration inside the streaming
  window.
- **Frames** (Frames lane): dropped frames while the answer streams.
- **Initial load**: Network tab with "Disable cache", then the time to the first interactive Code
  screen and the list of chunks loaded before it. Settings, Knowledge, Automation, Work, Onboarding
  and Activity should not be among them.

## How to report

Three runs per build, with all three numbers, not the mean alone. A difference smaller than the
spread between the three runs of the same build is not a result. Do not write "faster" without it.

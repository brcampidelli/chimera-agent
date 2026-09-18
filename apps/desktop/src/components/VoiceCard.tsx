import { Volume2 } from "lucide-react";
import { useEffect, useId, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import {
  BrowserSpeaker,
  preferredVoiceName,
  setPreferredVoiceName,
  voiceScore,
  voicesOf,
} from "@/lib/voice/speaker";
import { speechLocale } from "@/lib/voice/speech-text";

/**
 * Which voice reads the answers aloud — chosen from what this window offers, kept on this
 * machine.
 *
 * The voices are the operating system's and the browser's, not the server's: a choice made here
 * would mean nothing on another computer, so it lives in this window's storage rather than in the
 * server's settings, and the list is whatever `speechSynthesis` reports — on Windows, the two
 * old system voices plus the neural "Online (Natural)" ones the Edge engine brings. "Automatic"
 * is the ranked pick the voice mode makes on its own (`pickVoice`); a name is that voice, as long
 * as the window still has it. The Listen button reads one sentence in the app's language, so the
 * choice is made by ear rather than by name.
 */
export function VoiceCard() {
  const { t, lang } = useI18n();
  const headingId = useId();
  const selectId = useId();
  const locale = useMemo(() => speechLocale(lang), [lang]);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>(() => voicesOf(locale));
  const [chosen, setChosen] = useState<string>(() => preferredVoiceName());
  const speaker = useMemo(() => new BrowserSpeaker(), []);

  // The list fills after the first ask on a fresh document; follow it.
  useEffect(() => {
    setVoices(voicesOf(locale));
    const synth = typeof window === "undefined" ? undefined : window.speechSynthesis;
    if (!synth?.addEventListener) return;
    const refresh = () => setVoices(voicesOf(locale));
    synth.addEventListener("voiceschanged", refresh);
    return () => synth.removeEventListener("voiceschanged", refresh);
  }, [locale]);

  const ranked = useMemo(
    () => [...voices].sort((a, b) => voiceScore(b) - voiceScore(a)),
    [voices],
  );
  const known = chosen === "" || ranked.some((v) => v.name === chosen);

  return (
    <section className="surface overflow-hidden" aria-labelledby={headingId}>
      <h2 id={headingId} className="border-b border-hairline px-4 py-2.5 text-sm font-semibold">
        {t("settings.card.voice")}
      </h2>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <label htmlFor={selectId} className="text-sm font-medium">
            {t("settings.row.voice")}
          </label>
          <div className="text-xs text-muted-foreground">{t("settings.hint.voice")}</div>
          {ranked.length === 0 ? (
            <div className="text-xs text-muted-foreground" data-testid="voice-none">
              {t("settings.voice.none")}
            </div>
          ) : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <select
            id={selectId}
            className="field h-8 w-72 px-2.5 text-sm"
            value={known ? chosen : ""}
            data-testid="voice-select"
            onChange={(e) => {
              setChosen(e.target.value);
              setPreferredVoiceName(e.target.value);
            }}
          >
            <option value="">{t("settings.voice.auto")}</option>
            {ranked.map((v) => (
              <option key={v.name} value={v.name}>
                {v.name}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            variant="ghost"
            disabled={ranked.length === 0}
            title={t("settings.voice.listen")}
            data-testid="voice-listen"
            onClick={() => {
              speaker.cancel();
              void speaker.speak(t("settings.voice.sample"), locale);
            }}
          >
            <Volume2 className="h-4 w-4" />
            {t("settings.voice.listen")}
          </Button>
        </div>
      </div>
    </section>
  );
}

import { Volume2 } from "lucide-react";
import { useEffect, useId, useMemo, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { DICTS, LANGS, useI18n, useNum, type Lang } from "@/lib/i18n";
import {
  BrowserSpeaker,
  chosenVoiceLang,
  preferredVoiceName,
  preferredVoiceRate,
  setPreferredVoiceLang,
  setPreferredVoiceName,
  setPreferredVoiceRate,
  VOICE_RATES,
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
 *
 * Two more rows, kept the same way: how fast the voice reads (four steps, the engine's normal
 * pace by default) and the language it speaks and listens in — the interface's by default, which
 * is the only thing it could be before. A separate language is for the person who reads the app in
 * one language and talks to it in another; it changes which voices are listed, the language each
 * answer is read in, and the hint dictation sends to the transcriber.
 *
 * `children` are the rows that belong to the same card but to the server's settings — the model
 * a spoken turn is answered by — rendered by the Settings screen with its own row and field.
 */
export function VoiceCard({ children }: { children?: ReactNode }) {
  const { t, lang } = useI18n();
  const num = useNum();
  const headingId = useId();
  const selectId = useId();
  const rateId = useId();
  const langId = useId();
  // "" is "same as the interface": stored as nothing, so a window keeps following the interface
  // when the interface changes, rather than freezing the language it had on the day.
  const [langChoice, setLangChoice] = useState<Lang | "">(() => chosenVoiceLang());
  const voiceLang = langChoice || lang;
  const [rate, setRate] = useState<number>(() => preferredVoiceRate());
  const locale = useMemo(() => speechLocale(voiceLang), [voiceLang]);
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
      <div className="divide-y divide-hairline">
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <label htmlFor={selectId} className="text-sm font-medium">
            {t("settings.row.voice")}
          </label>
          <div className="text-xs text-muted-foreground">{t("settings.hint.voice")}</div>
          {ranked.length === 0 ? (
            <div className="text-xs text-muted-foreground" data-testid="voice-none">
              {t(langChoice ? "settings.voice.noneLang" : "settings.voice.none")}
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
              // The sentence in the language the voice speaks: a Portuguese voice reading the
              // English sample would judge nothing.
              void speaker.speak(DICTS[voiceLang]["settings.voice.sample"] ?? t("settings.voice.sample"), locale);
            }}
          >
            <Volume2 className="h-4 w-4" />
            {t("settings.voice.listen")}
          </Button>
        </div>
      </div>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <label htmlFor={rateId} className="text-sm font-medium">
            {t("settings.row.voiceRate")}
          </label>
          <div className="text-xs text-muted-foreground">{t("settings.hint.voiceRate")}</div>
        </div>
        <select
          id={rateId}
          className="field h-8 w-72 shrink-0 px-2.5 text-sm"
          value={String(rate)}
          data-testid="voice-rate"
          onChange={(e) => {
            const next = Number(e.target.value);
            setRate(next);
            setPreferredVoiceRate(next);
          }}
        >
          {VOICE_RATES.map((r) => (
            <option key={r} value={String(r)}>
              {r === 1 ? t("settings.voice.rateNormal", { rate: num(r) }) : `${num(r)}×`}
            </option>
          ))}
        </select>
      </div>
      <div className="flex items-center justify-between gap-4 px-4 py-3">
        <div className="min-w-0">
          <label htmlFor={langId} className="text-sm font-medium">
            {t("settings.row.voiceLang")}
          </label>
          <div className="text-xs text-muted-foreground">{t("settings.hint.voiceLang")}</div>
        </div>
        <select
          id={langId}
          className="field h-8 w-72 shrink-0 px-2.5 text-sm"
          value={langChoice}
          data-testid="voice-lang"
          onChange={(e) => {
            const next = e.target.value as Lang | "";
            setLangChoice(next);
            setPreferredVoiceLang(next);
          }}
        >
          <option value="">{t("settings.voice.langSame")}</option>
          {LANGS.map((l) => (
            <option key={l.code} value={l.code}>
              {l.label}
            </option>
          ))}
        </select>
      </div>
      {children}
      </div>
    </section>
  );
}

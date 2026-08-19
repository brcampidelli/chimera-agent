import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Cpu, Search } from "lucide-react";

import { getDoctor, getModels, patchConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { cn } from "@/lib/utils";
import { useT, type TFunc } from "@/lib/i18n";
import type { ModelOption } from "@/lib/types";

/** How many rows the list renders before it asks the user to narrow the search.
 *
 *  OpenRouter publishes four hundred models. Rendering all of them is a long frame for a menu that
 *  opens under the pointer, and a list nobody can scan is not a choice — it is the same free-text
 *  box with extra steps. Sixty is comfortably more than fits on screen, so the cut is only ever met
 *  by someone who has not typed yet, and it SAYS how many it is holding back rather than truncating
 *  silently. */
const MAX_ROWS = 60;

/** The tail of a slug, which is the part a human reads: `openrouter/deepseek/deepseek-chat-v3.1`
 *  → `deepseek-chat-v3.1`. Used only for the chip, where there is no room for the whole thing. */
function shortName(slug: string): string {
  return slug.split("/").pop() ?? slug;
}

/**
 * Which model answers the next message.
 *
 * The backend has accepted a per-turn `model` since the endpoint existed; nothing in the app ever
 * sent one, so every conversation ran on `CHIMERA_DEFAULT_MODEL` and changing it meant a trip to
 * Settings, a text field, and a slug typed from memory. That is the same failure the Ollama picker
 * and the provider picker were each built to end: availability is a fact about this install, and
 * asking the user to recall it is asking them to debug a 404 they will meet mid-turn.
 *
 * **The choice is per CONVERSATION, not a saved setting** — like the provider and the spend ceiling
 * beside it. A model quietly carried over from last week is how a turn ends up costing thirty times
 * what the person expected, and the receipt under each answer names what actually ran. The dialog
 * offers to make the pick the standing default, which is the same intent stated out loud.
 *
 * **What the rows say about tools is load-bearing.** A sixth of OpenRouter's index cannot call
 * tools, and a coding turn on one of those does not fail — it produces a confident description of an
 * edit that never happened. So `tools === false` is marked on the row and warned about next to the
 * chip, and `tools === null` (a source that did not say) is left blank rather than guessed.
 */
export function ModelPicker({
  value,
  onChange,
  disabled,
}: {
  /** The chosen slug, or "" for whatever the install's default is. */
  value: string;
  onChange: (slug: string) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);

  // Read from the cache the dialog fills, to answer one question: can the picked model call tools?
  // `enabled: false` means this never fetches on its own — a composer nobody has touched costs no
  // request, and before the list has been opened once there is simply nothing to warn about.
  const listing = useQuery({ queryKey: ["models", ""], queryFn: () => getModels(), enabled: false });
  const chosen = listing.data?.models?.find((m) => m.slug === value);

  // What "default" actually resolves to. `doctor` is already fetched by the worker row beside this
  // one and by three other screens, so naming the model costs no request — and without it the chip
  // says "default" and the user has to open the dialog to discover that the word means DeepSeek at
  // $0.25 rather than GPT-5.5 at $5. A control that hides what it is set to is a control you have to
  // click to read.
  const doctor = useQuery({ queryKey: ["doctor"], queryFn: getDoctor });
  const fallback = doctor.data?.default_model ?? "";

  // The chosen slug's own tail rather than its label, because the label is a sentence ("DeepSeek:
  // DeepSeek V3.1") and this is a chip in an already crowded row. Same for the default's name: the
  // word plus the model, so the chip reads as a setting rather than as a category.
  const buttonLabel = value
    ? shortName(value)
    : fallback
      ? `${t("model.pick.default")} · ${shortName(fallback)}`
      : t("model.pick.default");

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-xs uppercase tracking-wider text-muted-foreground">
          {t("model.pick.label")}
        </span>
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen(true)}
          className={cn(
            "flex items-center gap-1.5 rounded-chip border border-border px-2.5 py-1 text-xs",
            "transition-colors duration-1 ease-out disabled:opacity-50",
            focusRing,
            value ? "bg-accent/20 text-accent" : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Cpu className="h-3.5 w-3.5" />
          <span className="max-w-56 truncate font-mono">{buttonLabel}</span>
        </button>
        {/* Only for a model we were TOLD cannot call tools. An unknown answer stays silent: warning
            on a guess would train the user to ignore the one case that is real. */}
        {chosen?.tools === false ? (
          <span className="flex items-center gap-1.5 text-xs text-warn-foreground">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            {t("model.pick.noToolsWarning")}
          </span>
        ) : null}
      </div>
      <ModelDialog open={open} onOpenChange={setOpen} value={value} onPick={onChange} offerDefault />
    </>
  );
}

/**
 * The list itself: a search box, the models, and the way back to the install's default.
 *
 * Separate from the chip because onboarding needs the same list before there is a composer to put a
 * chip in — and needs it for a key that has not been saved yet, which is what `provider` is for.
 */
export function ModelDialog({
  open,
  onOpenChange,
  value,
  onPick,
  provider,
  offerDefault = false,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The chosen slug, or "" for the install's default. */
  value: string;
  onPick: (slug: string) => void;
  /** Force a remote catalogue to be listed regardless of the keys present — onboarding only, where
   *  the user is holding the key they are about to paste and "what does this buy" is the question. */
  provider?: string;
  /** Offer to write the pick to `CHIMERA_DEFAULT_MODEL`. Onboarding does not: it already saves the
   *  model alongside the key, and two controls writing one variable on one screen is one of them
   *  silently winning. */
  offerDefault?: boolean;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");

  // Fetched when the dialog first opens, not on mount: this is a round-trip to a public catalogue,
  // and paying for it before anybody asks for a model is paying for a menu most turns never open.
  const listing = useQuery({
    // The provider is part of the key. Onboarding asks a different question ("what does an
    // OpenRouter key buy?") and must not be served the composer's answer out of the cache.
    queryKey: ["models", provider ?? ""],
    queryFn: () => getModels(provider),
    enabled: open,
    // The server caches the remote fetch for an hour; this stops the app re-asking on every open
    // within a session while still picking up a newly configured key on the next one.
    staleTime: 5 * 60 * 1000,
  });

  const models = useMemo(() => listing.data?.models ?? [], [listing.data]);
  const defaultSlug = listing.data?.default ?? "";

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return models;
    // Matched against the slug as well as the label: someone pasting `deepseek/deepseek-chat-v3.1`
    // from a terminal is searching with the string they already have.
    return models.filter(
      (m) =>
        m.label.toLowerCase().includes(needle) ||
        m.slug.toLowerCase().includes(needle) ||
        m.vendor.toLowerCase().includes(needle),
    );
  }, [models, query]);

  const shown = filtered.slice(0, MAX_ROWS);
  const hidden = filtered.length - shown.length;

  const makeDefault = useMutation({
    mutationFn: (slug: string) => patchConfig({ CHIMERA_DEFAULT_MODEL: slug }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["config"] });
      void qc.invalidateQueries({ queryKey: ["models"] });
      void qc.invalidateQueries({ queryKey: ["doctor"] });
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("model.pick.title")}
      description={t("model.pick.blurb")}
    >
      <div className="space-y-3">
        <label className="flex items-center gap-2">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="sr-only">{t("model.pick.search")}</span>
          <input
            className="field h-9 w-full px-3 text-sm"
            autoFocus
            placeholder={t("model.pick.search")}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>

        {/* Why the list is short, when it is short. Said NEXT TO the list rather than instead of it:
            the models below are real and callable, and hiding them behind an error would answer
            "your key buys nothing", which is not what a failed fetch means. */}
        {listing.data?.reason ? (
          <p className="flex items-start gap-1.5 text-xs text-warn-foreground">
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            {t(`model.reason.${listing.data.reason}`)}
          </p>
        ) : null}

        <div className="max-h-80 space-y-1 overflow-y-auto">
          {/* Always first, and never filtered out by the search: it is the way back, and a way back
              you can lose by typing is not one. */}
          <Row
            label={t("model.pick.default")}
            hint={defaultSlug || t("model.pick.defaultUnknown")}
            selected={value === ""}
            onPick={() => {
              onPick("");
              onOpenChange(false);
            }}
          />
          {listing.isPending ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">{t("common.loading")}</p>
          ) : null}
          {listing.isError ? (
            <p className="px-2 py-3 text-xs text-bad">{t("model.pick.failed")}</p>
          ) : null}
          {!listing.isPending && !listing.isError && filtered.length === 0 ? (
            <p className="px-2 py-3 text-xs text-muted-foreground">{t("model.pick.empty")}</p>
          ) : null}
          {shown.map((model) => (
            <Row
              key={model.slug}
              label={model.label}
              hint={model.slug}
              badges={badgesFor(model, t)}
              recommended={model.recommended}
              selected={value === model.slug}
              onPick={() => {
                onPick(model.slug);
                onOpenChange(false);
              }}
            />
          ))}
          {hidden > 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">
              {t("model.pick.more", { n: hidden })}
            </p>
          ) : null}
        </div>

        {/* The standing decision, offered separately from the per-turn one. Enabled only when the
            two differ — a button that would write what is already written is a button that does
            nothing while looking like it did something. */}
        {offerDefault ? (
          <div className="flex flex-wrap items-center gap-2 border-t border-hairline pt-3">
            <Button
              size="sm"
              variant="outline"
              disabled={!value || value === defaultSlug || makeDefault.isPending}
              onClick={() => makeDefault.mutate(value)}
            >
              {t("model.pick.makeDefault")}
            </Button>
            <span className="text-xs text-muted-foreground">
              {makeDefault.isSuccess && value === defaultSlug
                ? t("model.pick.madeDefault")
                : t("model.pick.makeDefaultHint")}
            </span>
          </div>
        ) : null}
      </div>
    </Dialog>
  );
}

/** One row of the list: the name, the slug it will actually send, and what it costs to use. */
function Row({
  label,
  hint,
  badges,
  recommended,
  selected,
  onPick,
}: {
  label: string;
  hint: string;
  badges?: string[];
  recommended?: boolean;
  selected: boolean;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onPick}
      aria-pressed={selected}
      className={cn(
        "flex w-full items-start gap-2 rounded-xl2 px-2 py-1.5 text-left",
        "transition-colors duration-1 ease-out hover:bg-surface-hover",
        focusRing,
        selected && "bg-accent/20",
      )}
    >
      <Check
        className={cn("mt-0.5 h-4 w-4 shrink-0", selected ? "text-accent" : "opacity-0")}
        aria-hidden
      />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <span className="truncate text-sm">{label}</span>
          {recommended ? (
            <span className="rounded-chip bg-surface-2 px-1.5 text-xs text-accent">★</span>
          ) : null}
        </span>
        <span className="block truncate font-mono text-xs text-muted-foreground">{hint}</span>
        {badges && badges.length > 0 ? (
          <span className="block truncate text-xs text-muted-foreground">{badges.join(" · ")}</span>
        ) : null}
      </span>
    </button>
  );
}

/** The facts that decide whether to pick this one, in the order someone weighs them.
 *
 *  Exported for its test. Every branch here exists to keep an unknown from rendering as a number: a
 *  null price is "not published", never `$0`, and a null `tools` says nothing at all. */
export function badgesFor(model: ModelOption, t: TFunc): string[] {
  const badges: string[] = [];
  if (model.free) badges.push(t("model.pick.free"));
  else if (model.input_per_m != null && model.output_per_m != null) {
    badges.push(
      t("model.pick.price", {
        in: model.input_per_m.toFixed(2),
        out: model.output_per_m.toFixed(2),
      }),
    );
  } else badges.push(t("model.pick.priceUnknown"));
  if (model.context_k) badges.push(t("model.pick.context", { n: model.context_k }));
  if (model.tools === false) badges.push(t("model.pick.noTools"));
  if (model.vision) badges.push(t("model.pick.vision"));
  return badges;
}

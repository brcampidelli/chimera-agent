import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Search, Users } from "lucide-react";

import { getConfig, getModels, patchConfig } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { cn } from "@/lib/utils";
import { useT, type TFunc } from "@/lib/i18n";
import type { ModelOption } from "@/lib/types";

/** Who plays each part in a fused turn. `null` for a role means "whatever this install is set to". */
export interface Cast {
  panel: string[];
  judge: string;
  synthesizer: string;
}

export const EMPTY_CAST: Cast = { panel: [], judge: "", synthesizer: "" };

/** Same cut as the model list: sixty rows is more than fits on screen, and it says what it holds. */
const MAX_ROWS = 60;

/** A panel is at least two answers. One model plus a judge and a synthesiser is not fusion — it is
 *  one opinion, graded and rewritten, at three times the price. The server refuses it too. */
const MIN_PANEL = 2;

function shortName(slug: string): string {
  return slug.split("/").pop() ?? slug;
}

/** The lab a slug comes from: `openrouter/anthropic/claude-opus-5` → `anthropic`.
 *
 *  Mirrors `_vendor_of` in the engine, and only for the WARNING — the authoritative answer is
 *  `config.fusion.kinship`, computed by the engine itself. This exists because the warning has to
 *  react to a choice that has not been saved yet, and there is no round trip to ask about a cast
 *  that only exists in this dialog. */
function vendorOf(slug: string): string {
  const parts = slug.split("/");
  return parts.length >= 2 ? parts[parts.length - 2] : slug;
}

/**
 * Who answers, who grades, and who writes — chosen per conversation.
 *
 * Fusion's whole claim is an INDEPENDENT signal: several models answer the same question without
 * seeing each other, a judge that is not one of them says where they agree and where they contradict,
 * and a synthesiser writes the answer from that analysis. The engine has taken those three roles
 * per instance since it was written and the config has carried all three — but nothing between the
 * engine and a person could set them, so the only cast anyone could run was the one that shipped.
 * That is a strange place for a product to have no control: the shipped judge was once
 * `panel[0]` verbatim, and it graded its own answer for as long as nobody noticed.
 *
 * So the two things this dialog does that a plain multi-select would not:
 *
 * 1. **It says the cost of the shape, not just of the models.** A four-model panel is six calls —
 *    four answers, a judge, a synthesiser — and that arithmetic is invisible until the receipt.
 * 2. **It says when the panel is not independent**, at the moment of choosing rather than after
 *    paying. Two degrees: the judge sitting on its own panel, and the judge coming from the same lab
 *    as a panelist. Neither is refused — a user with one provider key has no way to avoid the
 *    overlap, and a refusal they cannot act on just removes the feature.
 */
export function FusionCast({
  value,
  onChange,
  disabled,
}: {
  value: Cast;
  onChange: (cast: Cast) => void;
  disabled?: boolean;
}) {
  const t = useT();
  const [open, setOpen] = useState(false);

  const config = useQuery({ queryKey: ["config"], queryFn: getConfig });
  const standing = config.data?.fusion;

  const panel = value.panel.length ? value.panel : (standing?.panel ?? []);
  const chosen = value.panel.length > 0 || !!value.judge || !!value.synthesizer;

  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        title={t("fusion.cast.hint")}
        className={cn(
          "flex items-center gap-1.5 rounded-chip border border-border px-2.5 py-1 text-xs",
          "transition-colors duration-1 ease-out hover:bg-surface-hover disabled:opacity-50",
          focusRing,
        )}
      >
        <Users className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-muted-foreground">{t("fusion.cast.label")}</span>
        <span className="font-mono">
          {chosen
            ? t("fusion.cast.n", { n: panel.length })
            : t("fusion.cast.standing")}
        </span>
      </button>
      <CastDialog
        open={open}
        onOpenChange={setOpen}
        value={value}
        onChange={onChange}
      />
    </>
  );
}

export function CastDialog({
  open,
  onOpenChange,
  value,
  onChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  value: Cast;
  onChange: (cast: Cast) => void;
}) {
  const t = useT();
  const qc = useQueryClient();
  const [query, setQuery] = useState("");
  const [role, setRole] = useState<"panel" | "judge" | "synthesizer">("panel");

  const config = useQuery({
    queryKey: ["config"],
    queryFn: getConfig,
    enabled: open,
  });
  const listing = useQuery({
    queryKey: ["models", ""],
    queryFn: () => getModels(),
    enabled: open,
  });

  const standing = config.data?.fusion;
  // The dialog always edits a COMPLETE cast, seeded from the standing one — otherwise a user who
  // only wants to swap the judge would be shown three empty roles and have to rebuild the panel to
  // express "the usual, but with a different judge".
  const cast: Cast = {
    panel: value.panel.length ? value.panel : (standing?.panel ?? []),
    judge: value.judge || (standing?.judge ?? ""),
    synthesizer: value.synthesizer || (standing?.synthesizer ?? ""),
  };

  const models = listing.data?.models ?? [];
  const term = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      term
        ? models.filter(
            (m) =>
              m.slug.toLowerCase().includes(term) ||
              m.label.toLowerCase().includes(term) ||
              (m.vendor ?? "").toLowerCase().includes(term),
          )
        : models,
    [models, term],
  );
  const rows = matches.slice(0, MAX_ROWS);
  const hidden = Math.max(0, matches.length - rows.length);

  // The two degrees of kinship, on the cast being edited rather than the one on disk.
  const judgeIsPanelist = !!cast.judge && cast.panel.includes(cast.judge);
  const sameLab = cast.judge
    ? cast.panel.filter(
        (m) => m !== cast.judge && vendorOf(m) === vendorOf(cast.judge),
      )
    : [];

  const calls = cast.panel.length + 2; // every panelist, then the judge, then the synthesiser
  const tooSmall = cast.panel.length < MIN_PANEL;

  const makeDefault = useMutation({
    mutationFn: () =>
      patchConfig({
        CHIMERA_FUSION_PANEL: cast.panel.join(","),
        CHIMERA_FUSION_JUDGE: cast.judge,
        CHIMERA_FUSION_SYNTHESIZER: cast.synthesizer,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config"] }),
  });

  const pick = (slug: string) => {
    if (role === "panel") {
      const next = cast.panel.includes(slug)
        ? cast.panel.filter((m) => m !== slug)
        : [...cast.panel, slug];
      onChange({ ...cast, panel: next });
      return;
    }
    onChange({ ...cast, [role]: cast[role] === slug ? "" : slug });
  };

  const selected = (slug: string) =>
    role === "panel" ? cast.panel.includes(slug) : cast[role] === slug;

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("fusion.cast.title")}
      description={t("fusion.cast.blurb")}
      className="flex max-h-dialog flex-col"
    >
      <div className="flex min-h-0 flex-1 flex-col gap-3">
        {/* The three roles as a pipeline, in the order they run, because the order is the mechanism:
            the panel cannot see each other, the judge cannot answer, the synthesiser cannot grade. */}
        <div className="flex flex-wrap gap-1.5">
          {(["panel", "judge", "synthesizer"] as const).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRole(r)}
              aria-pressed={role === r}
              className={cn(
                "flex flex-col items-start gap-0.5 rounded-xl2 border px-3 py-2 text-left",
                "transition-colors duration-1 ease-out",
                role === r
                  ? "border-accent bg-accent/10"
                  : "border-hairline hover:bg-surface-hover",
                focusRing,
              )}
            >
              <span className="text-xs font-semibold">
                {t(`fusion.role.${r}`)}
              </span>
              <span className="font-mono text-xs text-muted-foreground">
                {r === "panel"
                  ? t("fusion.cast.n", { n: cast.panel.length })
                  : shortName(cast[r]) || t("fusion.cast.unset")}
              </span>
            </button>
          ))}
        </div>

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

        {/* Said here, before the turn, rather than in the receipt after it. */}
        <div className="flex flex-col gap-1 text-xs">
          <p className="text-muted-foreground">
            {t("fusion.cast.calls", { n: calls })}
          </p>
          {tooSmall ? (
            <p className="flex items-start gap-1.5 text-warn-foreground">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {t("fusion.cast.tooSmall")}
            </p>
          ) : null}
          {judgeIsPanelist ? (
            <p className="flex items-start gap-1.5 text-warn-foreground">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {t("fusion.cast.judgeIsPanelist")}
            </p>
          ) : null}
          {!judgeIsPanelist && sameLab.length ? (
            <p className="flex items-start gap-1.5 text-warn-foreground">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              {t("fusion.cast.sameVendor", { vendor: vendorOf(cast.judge) })}
            </p>
          ) : null}
        </div>

        <div className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {rows.map((model) => (
            <ModelRow
              key={model.slug}
              model={model}
              selected={selected(model.slug)}
              multi={role === "panel"}
              onPick={() => pick(model.slug)}
              t={t}
            />
          ))}
          {hidden > 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">
              {t("model.pick.more", { n: hidden })}
            </p>
          ) : null}
          {!listing.isLoading && rows.length === 0 ? (
            <p className="px-2 py-2 text-xs text-muted-foreground">
              {t("model.pick.empty")}
            </p>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-2 border-t border-hairline pt-3">
          <Button
            size="sm"
            variant="outline"
            disabled={tooSmall || makeDefault.isPending}
            onClick={() => makeDefault.mutate()}
          >
            {t("fusion.cast.makeDefault")}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => onChange(EMPTY_CAST)}
          >
            {t("fusion.cast.reset")}
          </Button>
          <span className="text-xs text-muted-foreground">
            {makeDefault.isSuccess
              ? t("fusion.cast.madeDefault")
              : t("fusion.cast.thisChat")}
          </span>
        </div>
      </div>
    </Dialog>
  );
}

function ModelRow({
  model,
  selected,
  multi,
  onPick,
  t,
}: {
  model: ModelOption;
  selected: boolean;
  multi: boolean;
  onPick: () => void;
  t: TFunc;
}) {
  const price = model.free
    ? t("model.pick.free")
    : model.input_per_m == null || model.output_per_m == null
      ? t("model.pick.priceUnknown")
      : t("model.pick.price", {
          in: model.input_per_m.toFixed(2),
          out: model.output_per_m.toFixed(2),
        });

  return (
    <button
      type="button"
      onClick={onPick}
      role={multi ? "checkbox" : "radio"}
      aria-checked={selected}
      className={cn(
        "flex w-full items-start gap-2 rounded-xl2 px-2 py-1.5 text-left",
        "transition-colors duration-1 ease-out hover:bg-surface-hover",
        selected && "bg-accent/10",
        focusRing,
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-1 h-3.5 w-3.5 shrink-0 border",
          multi ? "rounded-[3px]" : "rounded-full",
          selected ? "border-accent bg-accent" : "border-border",
        )}
      />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm">{model.label}</span>
        <span className="block truncate font-mono text-xs text-muted-foreground">
          {model.slug}
        </span>
        <span className="block text-xs text-muted-foreground">{price}</span>
      </span>
    </button>
  );
}

import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { moveVaultKeys } from "@/lib/api";
import { useT } from "@/lib/i18n";
import type { VaultMove } from "@/lib/types";

/**
 * Move the keys an owner already has between `.env` and the OS vault (study 29, P7.7).
 *
 * The switch beside this only decides where the NEXT key goes; the keys already in `.env` stay in
 * plain text until something moves them, and the way back has to exist for the day the vault is
 * locked or the owner changes their mind. So both directions, and the result by name: a move that
 * said "done" while one key stayed behind in the file would be the claim the switch exists to stop.
 *
 * Into the vault needs the switch on (the server refuses otherwise); back to `.env` does not —
 * that is exactly when someone wants it. No value is ever sent or shown: the server reads each key
 * from where it is and writes it where it goes.
 */
export function KeyVaultMove({
  enabled,
  inVault,
}: {
  /** The owner's switch, `CHIMERA_KEY_VAULT`. */
  enabled: boolean;
  /** How many keys the vault holds now. */
  inVault: number;
}) {
  const t = useT();
  const qc = useQueryClient();
  const move = useMutation({
    mutationFn: (to: "vault" | "file") => moveVaultKeys(to),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["config"] });
      void qc.invalidateQueries({ queryKey: ["doctor"] });
    },
  });

  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="outline"
          disabled={!enabled || move.isPending}
          onClick={() => move.mutate("vault")}
        >
          {t("settings.vault.toVault")}
        </Button>
        <Button
          size="sm"
          variant="outline"
          disabled={inVault === 0 || move.isPending}
          onClick={() => move.mutate("file")}
        >
          {t("settings.vault.toFile")}
        </Button>
      </div>
      {move.data ? <MoveResult result={move.data} /> : null}
      {move.isError ? (
        <p className="text-xs text-bad-foreground">{(move.error as Error).message}</p>
      ) : null}
    </div>
  );
}

function MoveResult({ result }: { result: VaultMove }) {
  const t = useT();
  const moved = result.moved ?? [];
  const failed = result.failed ?? [];
  const skipped = result.skipped ?? [];
  // Kept apart from `failed`: the server knows why these stayed (larger than the vault holds per
  // entry), and "stayed where they were" would leave the owner looking for a locked vault.
  const tooLarge = result.too_large ?? [];
  if (!moved.length && !failed.length && !skipped.length && !tooLarge.length) {
    return <p className="text-xs text-muted-foreground">{t("settings.vault.nothing")}</p>;
  }
  return (
    <div className="text-right text-xs">
      {moved.length ? (
        <p className="text-muted-foreground">
          {t("settings.vault.moved", { keys: moved.join(", ") })}
        </p>
      ) : null}
      {failed.length ? (
        <p className="text-bad-foreground">
          {t("settings.vault.failed", { keys: failed.join(", ") })}
        </p>
      ) : null}
      {tooLarge.length ? (
        <p className="text-warn-foreground">
          {t("settings.vault.tooLarge", { keys: tooLarge.join(", ") })}
        </p>
      ) : null}
      {skipped.length ? (
        <p className="text-warn-foreground">
          {t("settings.vault.skipped", { keys: skipped.join(", ") })}
        </p>
      ) : null}
    </div>
  );
}

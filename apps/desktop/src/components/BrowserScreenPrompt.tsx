import { useMutation } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { focusRing } from "@/components/ui/focus";
import { exchangeBrowserScreenCode } from "@/lib/api";
import { SCREEN_NEEDED_EVENT } from "@/lib/browser-screen";
import { useT } from "@/lib/i18n";

/**
 * The browser tab's way to the owner's screen (`chimera/api/browser_screen.py`).
 *
 * A screen-only action refused in a tab that holds no screen fires `SCREEN_NEEDED_EVENT` (see
 * `api.ts`), and this asks for the code the desktop app shows. The code is typed, sent once, and
 * forgotten here: it is never stored, logged or echoed back. On success the server sets an HttpOnly
 * cookie, so the tab is the owner's screen for its session without the page ever holding a secret.
 */
export function BrowserScreenPrompt() {
  const t = useT();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const [outcome, setOutcome] = useState<"idle" | "paired" | "refused">("idle");

  useEffect(() => {
    const onNeeded = () => {
      setOpen(true);
      setOutcome("idle");
    };
    window.addEventListener(SCREEN_NEEDED_EVENT, onNeeded);
    return () => window.removeEventListener(SCREEN_NEEDED_EVENT, onNeeded);
  }, []);

  const pair = useMutation({
    mutationFn: (value: string) => exchangeBrowserScreenCode(value),
    onSuccess: () => {
      setCode("");
      setOutcome("paired");
    },
    onError: () => {
      setCode("");
      setOutcome("refused");
    },
  });

  if (!open) return null;

  const close = (next: boolean) => {
    if (!next) {
      setCode("");
      setOpen(false);
    }
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={t("browserPairing.prompt.title")}
      description={t("browserPairing.prompt.body")}
      footer={
        outcome === "paired" ? (
          <Button size="sm" onClick={() => close(false)}>
            {t("browserPairing.prompt.done")}
          </Button>
        ) : (
          <>
            <Button size="sm" variant="ghost" onClick={() => close(false)}>
              {t("browserPairing.prompt.cancel")}
            </Button>
            <Button
              size="sm"
              disabled={pair.isPending || code.trim().length === 0}
              onClick={() => pair.mutate(code)}
            >
              {t("browserPairing.prompt.submit")}
            </Button>
          </>
        )
      }
    >
      {outcome === "paired" ? (
        <p role="status" className="text-sm text-ok-foreground">
          {t("browserPairing.prompt.paired")}
        </p>
      ) : (
        <div className="space-y-2">
          <label className="block text-xs text-muted-foreground" htmlFor="browser-pairing-code">
            {t("browserPairing.prompt.label")}
          </label>
          <input
            id="browser-pairing-code"
            autoComplete="off"
            spellCheck={false}
            value={code}
            onChange={(event) => setCode(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && code.trim() && !pair.isPending) pair.mutate(code);
            }}
            className={`w-full rounded-md border border-border bg-background px-3 py-2 font-mono text-base tracking-widest text-foreground ${focusRing}`}
          />
          {outcome === "refused" ? (
            <p role="alert" className="text-xs text-bad-foreground">
              {t("browserPairing.prompt.wrong")}
            </p>
          ) : null}
        </div>
      )}
    </Dialog>
  );
}

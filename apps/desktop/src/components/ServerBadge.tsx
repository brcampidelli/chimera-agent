import { Cloud } from "lucide-react";

import { active } from "@/lib/server";

/**
 * Which Chimera you are looking at, when it is not this computer's.
 *
 * Silent for the local sidecar — that is the default and every existing user's whole experience,
 * and a badge saying "local" on every screen forever would be chrome nobody asked for.
 *
 * Loud for a remote, on every screen, because of the one mistake this feature can cause that
 * nothing else in the app can: running something against the wrong Chimera. Every screen here is a
 * command surface — dispatch a board, start a run, edit a file, change a setting — and the only
 * thing distinguishing your machine from a server across the internet is the data on screen, which
 * looks identical either way.
 */
export function ServerBadge() {
  const server = active();
  if (!server.baseUrl) return null;
  return (
    <span
      className="flex items-center gap-1 rounded-chip border border-accent2/40 px-2 py-0.5 text-accent2"
      title={server.baseUrl}
    >
      <Cloud className="h-3 w-3" aria-hidden />
      {server.name}
    </span>
  );
}

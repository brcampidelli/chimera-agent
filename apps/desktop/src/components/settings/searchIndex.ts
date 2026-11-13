/**
 * The index the Settings search reads: one entry per thing a person can look for, naming i18n keys
 * rather than strings, so a result is in the interface language and a search for "idioma" finds
 * "Idioma" without a second vocabulary to maintain.
 *
 * It is written by hand and checked by a test (`searchIndex.test.ts`): every `settings.row.*` key
 * that labels a `<Row>` in Settings has to be here, so a row added without an entry fails CI instead
 * of being unfindable. It used to cover 56 of about 110 rows and four tabs, which nobody noticed
 * because a search that finds nothing looks the same as a setting that does not exist.
 */

/** Every tab of the Settings screen, so each can be a result of its own. */
export type SearchableTab =
  | "general"
  | "appearance"
  | "skills"
  | "connectors"
  | "plugins"
  | "connections"
  | "usage"
  | "security"
  | "decisions";

/**
 * Where a result lands. `anchor` is the id of the element to reveal: a switch, or a whole card.
 * `advanced` says it sits inside the Advanced block, which is closed until opened, so the screen has
 * to open it first. Before this existed, a result switched tabs and stopped there, and the card it
 * named was out of sight.
 */
export interface SearchTarget {
  tab: SearchableTab;
  anchor?: string;
  advanced?: boolean;
}

export interface SearchEntry extends SearchTarget {
  label: string;
  hint?: string;
  /** Words to search by beyond the label and hint: an i18n key whose value is a comma-separated list. */
  terms?: string;
  /** Fills `{placeholders}` in the label and hint, for rows that exist once per bot or per alias. */
  params?: Readonly<Record<string, string>>;
}

const inAdvanced = (anchor: string): SearchTarget => ({ tab: "general", anchor, advanced: true });

export const SEARCH_INDEX: readonly SearchEntry[] = [
  { tab: "appearance", label: "settings.row.language", hint: "settings.hint.language" },
  { tab: "appearance", label: "settings.row.theme", hint: "settings.hint.theme" },
  { tab: "appearance", label: "settings.row.accent" },
  { tab: "appearance", label: "settings.row.motion" },
  { tab: "appearance", label: "settings.row.textSize" },
  { tab: "appearance", label: "settings.row.transcriptWidth" },
  { tab: "appearance", label: "settings.row.transcriptView" },
  { tab: "general", label: "code.sessions.defaultProject", hint: "code.workspacePlaceholder" },
  { tab: "general", label: "settings.row.defaultModel" },
  { tab: "general", label: "code.style.label", hint: "profile.style.contextual" },
  { tab: "general", label: "settings.row.agentName", hint: "settings.hint.agentName" },
  { tab: "general", label: "settings.row.agentLanguage" },
  { tab: "general", label: "settings.row.voiceModel", hint: "settings.hint.voiceModel" },
  { tab: "general", label: "settings.row.voiceWorkModel", hint: "settings.hint.voiceWorkModel" },
  { tab: "general", label: "notify.row.finish" },
  { tab: "general", label: "notify.row.minSeconds", hint: "notify.hint.minSeconds" },
  { tab: "general", label: "notify.row.approvals", hint: "notify.hint.approvals" },
  { tab: "general", label: "settings.row.costMode", hint: "settings.hint.costMode" },
  {
    tab: "general",
    label: "env.settings.title",
    hint: "env.settings.hint",
    anchor: "settings-card-environments",
    terms: "settings.search.terms.environments",
  },
  { tab: "general", label: "env.ssh.hosts.title", hint: "env.ssh.hosts.hint", anchor: "settings-ssh-hosts" },
  {
    tab: "general",
    label: "settings.card.autonomy",
    anchor: "settings-card-autonomy",
    terms: "settings.search.terms.autonomy",
  },
  // The two agent-authority switches sit on the autonomy card, on General, not in the Advanced block.
  {
    tab: "general",
    label: "settings.row.pullRequests",
    hint: "settings.hint.pullRequests",
    anchor: "settings-pull-requests",
    terms: "settings.search.terms.pullRequests",
  },
  {
    tab: "general",
    label: "settings.row.closeLoop",
    hint: "settings.hint.closeLoopNoSecret",
    anchor: "settings-close-loop",
    terms: "settings.search.terms.closeLoop",
  },
  {
    tab: "general",
    label: "settings.row.archiveOnPrMerge",
    hint: "settings.hint.archiveOnPrMerge",
    anchor: "settings-archive-on-pr-merge",
    terms: "settings.search.terms.archiveOnPrMerge",
  },
  {
    tab: "general",
    label: "settings.row.claudeCompat",
    hint: "settings.hint.claudeCompat",
    anchor: "settings-claude-compat",
    terms: "settings.search.terms.claudeCompat",
  },
  {
    tab: "general",
    label: "settings.row.idleCompaction",
    hint: "settings.hint.idleCompaction",
    anchor: "settings-idle-compaction",
    terms: "settings.search.terms.idleCompaction",
  },
  {
    tab: "general",
    label: "settings.row.worktreeCleanup",
    hint: "settings.hint.worktreeCleanup",
    anchor: "settings-worktree-cleanup",
  },
  {
    tab: "general",
    label: "settings.row.hooks",
    hint: "settings.hint.hooks",
    anchor: "settings-hooks",
  },
  {
    tab: "general",
    label: "settings.row.hooksHostExec",
    hint: "settings.hint.hooksHostExec",
    anchor: "settings-hooks-host-exec",
  },
  {
    tab: "general",
    label: "settings.row.claudeHooksRepo",
    hint: "settings.hint.claudeHooksRepo",
    anchor: "settings-claude-hooks-repo",
  },
  {
    tab: "general",
    label: "settings.row.claudeHooksUser",
    hint: "settings.hint.claudeHooksUser",
    anchor: "settings-claude-hooks-user",
  },
  {
    tab: "general",
    label: "settings.row.claudeUserHostExec",
    hint: "settings.hint.claudeUserHostExec",
    anchor: "settings-claude-user-host-exec",
  },
  {
    tab: "general",
    label: "settings.row.closeLoopMerge",
    hint: "settings.hint.closeLoopMerge",
    anchor: "settings-close-loop-merge",
    terms: "settings.search.terms.closeLoopMerge",
  },
  // The Security screen's Access card: the browser's way to the owner's screen (a code, then a session).
  {
    tab: "security",
    label: "browserPairing.title",
    anchor: "settings-browser-pairing",
    terms: "settings.search.terms.browserPairing",
  },
  // Named endpoints sit on Connections, not on General, so the result names that tab and reveals the card.
  {
    tab: "connections",
    label: "settings.card.endpoints",
    anchor: "settings-card-endpoints",
    terms: "settings.search.terms.endpoints",
  },
  // The Advanced block: every card in it is indexed, because a closed block hides what the search
  // leaves out. The two bridge switches are indexed by row as well, so "ponte" lands on the switch.
  // The bots' voice switches (Discord's card; Telegram's sits beside it under the same keys).
  {
    ...inAdvanced("settings-voice-listen-discord"),
    label: "settings.row.voiceListen",
    hint: "settings.hint.voiceListenDownload",
    terms: "settings.search.terms.voice",
  },
  {
    ...inAdvanced("settings-voice-reply-discord"),
    label: "settings.row.voiceReply",
    hint: "settings.hint.voiceReply",
    terms: "settings.search.terms.voice",
  },
  {
    ...inAdvanced("settings-chat-voice-model-discord"),
    label: "settings.row.chatVoiceModel",
    hint: "settings.hint.chatVoiceModel",
    terms: "settings.search.terms.voice",
  },
  { ...inAdvanced("settings-card-pools"), label: "settings.card.pools" },
  { ...inAdvanced("settings-card-memory"), label: "settings.card.memory" },
  { ...inAdvanced("settings-card-cache-sandbox"), label: "settings.card.cacheSandbox" },
  { ...inAdvanced("settings-card-mcp"), label: "settings.card.mcp" },
  { ...inAdvanced("settings-card-automation"), label: "settings.card.automation" },
  { ...inAdvanced("settings-card-keep-awake"), label: "settings.card.keepAwake" },
  { ...inAdvanced("settings-card-experimental"), label: "settings.card.experimental" },
  { ...inAdvanced("settings-card-system-one"), label: "settings.card.systemOne" },
  { ...inAdvanced("settings-card-storage"), label: "settings.card.storage" },
  { ...inAdvanced("settings-card-diagnostics"), label: "settings.card.diagnostics" },
  { ...inAdvanced("settings-card-server"), label: "settings.card.server" },
  { ...inAdvanced("settings-card-sharing"), label: "settings.card.sharing" },
  { ...inAdvanced("settings-card-bridge"), label: "settings.card.bridge", terms: "settings.search.terms.bridge" },
  {
    ...inAdvanced("settings-desktop-bridge"),
    label: "settings.row.desktopBridge",
    hint: "settings.hint.desktopBridge",
    terms: "settings.search.terms.bridge",
  },
  {
    ...inAdvanced("settings-desktop-bridge-full"),
    label: "settings.row.desktopBridgeFull",
    hint: "settings.hint.desktopBridgeFull",
    terms: "settings.search.terms.bridge",
  },
  // ---- The rows the first 56 entries left out (checked by `searchIndex.test.ts`). ----
  { tab: "general", label: "settings.row.reach", hint: "settings.hint.reach", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.approval", hint: "settings.hint.approval", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.hostExec", hint: "settings.hint.hostExec", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.strictSpendCap", hint: "settings.hint.strictSpendCap", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.governance", hint: "settings.hint.governance", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.approvalWebhook", hint: "settings.hint.approvalWebhook", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.egressAllow", hint: "settings.hint.egressAllow", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.deniedTools", hint: "settings.hint.deniedTools", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.wireLog", hint: "settings.hint.wireLog", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.bandDeadline", hint: "settings.hint.bandDeadline", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.exfilHostPath", hint: "settings.hint.exfilHostPath", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.shellFetchGuard", hint: "settings.hint.shellFetchGuard", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.armOnRecalledLessons", hint: "settings.hint.armOnRecalledLessons", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.ropeLite", hint: "settings.hint.ropeLite", anchor: "settings-card-governance-audit" },
  { tab: "general", label: "settings.row.browserLink", hint: "settings.hint.browserLink", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.browserTrustedSites", hint: "settings.hint.browserTrustedSites", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.acceptance", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.suggestionAcceptance", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.sandboxNetwork", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.botToken", hint: "settings.hint.botToken", anchor: "settings-card-messaging-discord", advanced: true, params: { platform: "Discord" } },
  { tab: "general", label: "settings.row.botToken", hint: "settings.hint.botToken", anchor: "settings-card-messaging-telegram", advanced: true, params: { platform: "Telegram" } },
  { tab: "general", label: "settings.row.botRun", hint: "settings.hint.botRun", anchor: "settings-card-messaging-discord", advanced: true, params: { platform: "Discord" } },
  { tab: "general", label: "settings.row.botRun", hint: "settings.hint.botRun", anchor: "settings-card-messaging-telegram", advanced: true, params: { platform: "Telegram" } },
  { tab: "general", label: "settings.row.botAllowed", hint: "settings.hint.botAllowed", anchor: "settings-card-messaging-discord", advanced: true, params: { platform: "Discord" } },
  { tab: "general", label: "settings.row.botAllowed", hint: "settings.hint.botAllowed", anchor: "settings-card-messaging-telegram", advanced: true, params: { platform: "Telegram" } },
  { tab: "general", label: "settings.row.discordAttach", hint: "settings.hint.discordAttach", anchor: "settings-card-messaging-discord", advanced: true, params: { platform: "Discord" } },
  { tab: "appearance", label: "settings.row.tint", hint: "settings.hint.tint", anchor: "settings-card-appearance" },
  { tab: "appearance", label: "settings.row.contrast", hint: "settings.hint.contrast", anchor: "settings-card-appearance" },
  { tab: "appearance", label: "settings.row.uiZoom", hint: "settings.hint.uiZoom", anchor: "settings-card-appearance" },
  { tab: "appearance", label: "settings.row.uiFont", hint: "settings.hint.uiFont", anchor: "settings-card-appearance" },
  { tab: "appearance", label: "settings.row.codeFont", hint: "settings.hint.codeFont", anchor: "settings-card-appearance" },
  { tab: "general", label: "settings.row.providersWithKey", anchor: "settings-card-status" },
  { tab: "general", label: "settings.row.modelLadder", hint: "settings.hint.modelLadder", anchor: "settings-card-status" },
  { tab: "general", label: "settings.row.weakModel", hint: "settings.hint.roleModels", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.midModel", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.orchestratorModel", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.apiBase", hint: "settings.hint.apiBase", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.ollamaUrl", hint: "settings.hint.ollamaUrl", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.ollamaModels", hint: "settings.hint.ollamaModels", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.completeModel", hint: "settings.hint.completeModel", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.fallbackModels", hint: "settings.hint.fallbackModels", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.cascade", hint: "settings.hint.cascade", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.orDataCollection", hint: "settings.hint.orDataCollection", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.orZdr", hint: "settings.hint.orZdr", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.agentReadsOwnEnv", anchor: "settings-card-model" },
  { tab: "general", label: "settings.row.keyVault", anchor: "settings-card-api-keys" },
  { tab: "general", label: "settings.row.keyVaultMove", hint: "settings.hint.keyVaultMove", anchor: "settings-card-api-keys" },
  { tab: "general", label: "settings.row.poolsIntro", hint: "settings.hint.pools", anchor: "settings-card-pools", advanced: true },
  { tab: "general", label: "settings.row.backend", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.semantic", hint: "settings.hint.semantic", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.embedModel", hint: "settings.hint.embedModel", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.autoConsolidate", hint: "settings.hint.autoConsolidate", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.skillCards", hint: "settings.hint.skillCardsRevised", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.rememberChat", hint: "settings.hint.rememberChat", anchor: "settings-card-memory", advanced: true },
  { tab: "general", label: "settings.row.completionCache", hint: "settings.hint.completionCache", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.promptCache", hint: "settings.hint.promptCache", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.sandbox", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.sandboxImage", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.showBrowser", hint: "settings.hint.showBrowser", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.browserSites", hint: "settings.hint.browserSites", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.browserGoogle", hint: "settings.hint.browserGoogle", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.browserCookieRetention", hint: "settings.hint.browserCookieRetention", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.browserLocalPorts", hint: "settings.hint.browserLocalPorts", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.guardChat", hint: "settings.hint.guardChat", anchor: "settings-card-cache-sandbox", advanced: true },
  { tab: "general", label: "settings.row.mcpAutoload", hint: "settings.hint.mcpAutoload", anchor: "settings-card-mcp", advanced: true },
  { tab: "general", label: "settings.row.appCron", hint: "settings.hint.appCron", anchor: "settings-card-automation", advanced: true },
  { tab: "general", label: "settings.row.cronNotifyFailures", hint: "settings.hint.cronNotifyFailures", anchor: "settings-card-automation", advanced: true },
  { tab: "general", label: "settings.row.browserSituation", hint: "settings.hint.browserSituation", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.researchAgent", hint: "settings.hint.researchAgent", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.explorerContract", hint: "settings.hint.explorerContract", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.deferTools", hint: "settings.hint.deferTools", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.mcpDefer", hint: "settings.hint.mcpDefer", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.projectPack", hint: "settings.hint.projectPack", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.claudeAgents", hint: "settings.hint.claudeAgents", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.claudeAgentRole", hint: "settings.hint.claudeAgentRole", anchor: "settings-card-experimental", advanced: true, params: { alias: "Opus" } },
  { tab: "general", label: "settings.row.claudeAgentRole", hint: "settings.hint.claudeAgentRole", anchor: "settings-card-experimental", advanced: true, params: { alias: "Sonnet" } },
  { tab: "general", label: "settings.row.claudeAgentRole", hint: "settings.hint.claudeAgentRole", anchor: "settings-card-experimental", advanced: true, params: { alias: "Haiku" } },
  { tab: "general", label: "settings.row.claudeAgentRole", hint: "settings.hint.claudeAgentRole", anchor: "settings-card-experimental", advanced: true, params: { alias: "Fable" } },
  { tab: "general", label: "settings.row.previewAutoVerify", hint: "settings.hint.previewAutoVerify", anchor: "settings-card-experimental", advanced: true },
  { tab: "general", label: "settings.row.bearer", hint: "settings.hint.bearer", anchor: "settings-card-server", advanced: true },
  { tab: "general", label: "settings.row.sharing", hint: "settings.hint.sharing", anchor: "settings-card-sharing", advanced: true },
  { tab: "general", label: "settings.row.shareExpiry", hint: "settings.hint.shareExpiry", anchor: "settings-card-sharing", advanced: true },
  { tab: "general", label: "settings.row.worktreeInclude", hint: "settings.hint.worktreeInclude", anchor: "settings-card-autonomy" },
  { tab: "general", label: "settings.row.agentInstructions", hint: "settings.hint.agentInstructions", anchor: "settings-card-identity" },
  // The tabs themselves: Skills, Connectors, Plugins, Usage and Decisions hold lists, not rows, so
  // the screen as a whole is what a search for them can land on.
  { tab: "skills", label: "settings.tab.skills" },
  { tab: "connectors", label: "settings.tab.connectors" },
  { tab: "plugins", label: "settings.tab.plugins" },
  { tab: "usage", label: "nav.usage" },
  { tab: "decisions", label: "settings.tab.decisions" },
];

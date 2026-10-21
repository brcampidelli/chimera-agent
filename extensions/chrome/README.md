# Chimera Chrome extension (E0 read-only foundation)

1. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this directory. Copy the extension ID shown on the card.
2. From the repository root, run `python scripts/register_chrome_host.py <32-character-extension-id>` to print the registration plan. `--apply` writes the Native Messaging manifest and the `HKCU\Software\Google\Chrome\NativeMessagingHosts\app.chimera.browser` registry value. The host executable must already be installed at the `--executable` path.

The desktop's **Settings → Browser** screen shows whether Chrome is unpaired, paired but disconnected, or connected. It can refresh status, stop the current session (detach the debugger and close only Chimera-owned tabs), or unpair. Unpairing removes the pairing key; registering again is required to reconnect.

The screen has two separate site lists: sites the browser may open, and owner-trusted sites where ordinary clicks and typing do not need a new approval. Trust is per hostname, not a wildcard. Purchases, payments, sending, deletion and publishing still require confirmation; passwords and payment-card data are never entered. The approval card's **Always for this site** action adds that hostname and approves the pending action. The owner can revoke a site in Settings. Bots, scheduled jobs and the MCP bridge cannot change this list.

Google search is separately off by default; when enabled it asks before every search. This does not scrape Google results as a search provider.

This is an E0 protocol foundation, not a paired product: the native host deliberately refuses until an authenticated app pairing/forwarding channel is implemented. Site access is optional and must be granted explicitly. Only reading and navigation operations are defined; click/type are unavailable until E2. The Stop button detaches the debugger and closes the Chimera-owned tab group.

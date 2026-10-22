# Chimera Chrome extension

The extension reads and acts on tabs opened by Chimera Desktop, only while paired with the local app. Page content—including accessibility trees, text, and screenshots—is sent from the extension to the local app and then to the AI model provider configured by the owner (for example, OpenRouter). This is the store's “Website content” data category. The extension itself makes no network requests, has no analytics, and does not read or export cookies, passwords, browsing history, or autofill data.

## Install and pair

1. Install the extension from the Chrome Web Store, or for a local build open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this directory. Copy the extension ID shown on the card.
2. From the repository root, run `python scripts/register_chrome_host.py <extension-id>` to print the registration plan. `--apply` writes the Native Messaging manifest and the `HKCU\Software\Google\Chrome\NativeMessagingHosts\app.chimera.browser` registry value. The host executable must already be installed at the `--executable` path. The Web Store ID differs from a locally loaded extension ID; run `python scripts/register_chrome_host.py <store-extension-id> --apply` to register the store ID.
3. In **Settings → Browser**, click **Pair** to display a six-digit code valid for two minutes. Enter it in the Chrome popup and choose **Pair**; the one-time code is consumed on success and locks after five attempts. The popup also offers **Allow this site** when Chrome needs permission, and **Stop** to disconnect without unpairing.

The desktop's **Settings → Browser** screen shows whether Chrome is unpaired, paired but disconnected, or connected. The key is kept in the operating-system vault if available; otherwise the status warns that it is in a local file. Unpairing removes the key, so a new code is required. This makes direct access harder, not impossible: a malicious process running as the same user remains a serious threat.

The screen has two separate site lists: sites the browser may open, and owner-trusted sites where ordinary clicks and typing do not need a new approval. Trust is per hostname, not a wildcard. Purchases, payments, sending, deletion and publishing still require confirmation; passwords and payment-card data are never entered. The approval card's **Always for this site** action adds that hostname and approves the pending action. The owner can revoke a site in Settings. Bots, scheduled jobs and the MCP bridge cannot change this list.

Google search is separately off by default; when enabled the extension opens Google in a visible tab and reads the results, following the action plan in §7.1. This content is also relayed to the configured AI provider. Chrome's `debugger` permission technically permits reading and changing any tab; the extension code restricts its operations to the Chimera group and owner-granted origins, and Chrome displays its debugging banner while active.

For the extension's capability, permission, and privacy details, see [the security and privacy notes](../../docs/chrome-extension.md).

## Versioning

The manifest version is `0.73.0` for the corresponding Chimera release. Update it to the package's version in the same release when the package version changes; the Chrome Web Store package must ship with that release.

Site access is optional and must be granted explicitly. The Stop button detaches the debugger and closes tabs in the Chimera-owned group. Unpairing removes the pairing key; it does not automatically revoke optional site permissions, which can be removed in Chrome's extension settings.

## Store package

Regenerate the extension icons from the desktop source with `python scripts/generate_chrome_extension_icons.py` (requires Pillow), then run `python scripts/package_chrome_extension.py`. The deterministic package is written to `dist/chimera-chrome-extension-0.73.0.zip`. When the Chimera package version changes, update `manifest.json` and this README together. The ZIP contains only the manifest, runtime scripts, popup (HTML, CSS and script) and icons.

No package is uploaded or published by these scripts.

# Chrome extension: security and privacy

The Chimera Browser extension lets Chimera Desktop read and act on tabs that Chimera opens in its own **Chimera** tab group. It works only when paired with the local desktop app. The owner chooses site access explicitly. It does not inspect the owner's other tabs or provide a search-engine scraping path.

## Permissions

- **`debugger`** — uses Chrome DevTools Protocol to read and act on the Chimera-owned tab. Chrome displays its debugger warning while attached.
- **`nativeMessaging`** — communicates with the local Chimera native host and paired desktop app; it does not open a network listener.
- **`tabs`** — creates, navigates, and closes tabs belonging to the Chimera group.
- **`tabGroups`** — creates and maintains the group named “Chimera”.
- **`storage`** — remembers the group and active tab across service-worker restarts.
- **Optional `http://*/*` and `https://*/*` host access** — Chrome requests access only after the owner allows a site. Access is optional and granted site by site; the extension does not declare `<all_urls>`.

The extension does not read or export cookies, saved passwords, browsing history, or autofill data. Sensitive fields and CAPTCHA challenges are refused. Irreversible actions such as payments, purchases, sending, deletion, publishing, and non-search form submissions require owner confirmation, including on trusted sites. Downloads and uploads are refused.

## Privacy policy

Nothing is sent off the computer by the extension. It communicates only with the local app through Chrome Native Messaging. There is no extension analytics, no extension-owned network traffic, and no cookies are read or exported. Web pages opened by the owner-authorized browser session are handled by Chrome as normal; the extension does not proxy their network requests.

## Install, pair, and unpair

1. In Chrome, open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose the `extensions/chrome` directory.
2. Copy the extension ID. From the repository root, run `python scripts/register_chrome_host.py <extension-id>` to review the registration plan. Apply registration only when the native host executable is installed and the owner is ready to pair.
3. In Chimera Desktop, open **Settings → Browser** and complete pairing. The extension popup shows status, offers **Allow this site** when a pending permission request needs an explicit user gesture, and provides **Stop**.
4. To stop navigation, use **Stop** in the popup or desktop. To unpair, use the desktop's **Unpair** action; this removes the pairing key. Remove the native-host registration and unpacked extension separately if they are no longer needed.

The extension's manifest version must track the Chimera package version. For this release it is `0.72.0`; update `manifest.json` and `extensions/chrome/README.md` together when the package version changes. The package-generation scripts do not upload or publish anything. Store submission and publication are owner-only actions.

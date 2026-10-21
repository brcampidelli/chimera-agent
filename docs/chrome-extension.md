# Chrome extension: security and privacy

The Chimera Browser extension lets Chimera Desktop read and act on tabs that Chimera opens in its own **Chimera** tab group. It works only when paired with the local desktop app. The owner chooses site access explicitly. Page content—including accessibility trees, page text, and screenshots—travels from the extension to the local app and then to the AI model provider configured by the owner (for example, OpenRouter). This is the store's “Website content” data category. The extension does not inspect the owner's other tabs.

## Permissions

- **`debugger`** — Chrome permits this permission to read and change any tab. The extension's code restricts its own operations to the Chimera tab group and to origins the owner granted (`checkCurrentOrigin`); that restriction is enforced by the extension, not by Chrome's permission system. Chrome displays its debugger warning whenever debugging is active.
- **`nativeMessaging`** — communicates with the local Chimera native host and paired desktop app; it does not open a network listener.
- **`tabs`** — creates, navigates, and closes tabs belonging to the Chimera group.
- **`tabGroups`** — creates and maintains the group named “Chimera”.
- **`storage`** — remembers the group and active tab across service-worker restarts.
- **Optional `http://*/*` and `https://*/*` host access** — Chrome requests access only after the owner allows a site. Access is optional and granted site by site; the extension does not declare `<all_urls>`.

The extension itself makes no network requests, has no analytics, and does not read or export cookies, passwords, browsing history, or autofill data. Sensitive fields and CAPTCHA challenges are refused. Irreversible actions such as payments, purchases, sending, deletion, publishing, and non-search form submissions require owner confirmation, including on trusted sites. Downloads and uploads are refused. Content the owner asks Chimera to read is provided to the configured model provider through the app.

## Privacy policy

The extension communicates with the local app through Chrome Native Messaging and makes no network requests of its own. It has no analytics and does not read or export cookies, passwords, or browsing history. When the owner asks the app to inspect a page, the page content returned by the extension is sent onward by the app to the owner's configured AI model provider; it is not accurate to describe that content as staying on the computer.

## Install, pair, and unpair

1. Install the extension from the Chrome Web Store, or for a local build open `chrome://extensions`, enable **Developer mode**, select **Load unpacked**, and choose the `extensions/chrome` directory.
2. Copy the extension ID shown by Chrome. From the repository root, run `python scripts/register_chrome_host.py <extension-id>` to review the registration plan. Apply registration only when the native host executable is installed and the owner is ready to pair. The Web Store extension ID differs from a locally loaded extension ID; run `python scripts/register_chrome_host.py <store-extension-id> --apply` again to register the store ID.
3. In Chimera Desktop, open **Settings → Browser** and complete pairing. The extension popup shows status, names the site requesting permission in its **Allow <origin>** button, and provides **Stop**.
4. To stop navigation, use **Stop** in the popup or desktop. To unpair, use the desktop's **Unpair** action; this removes the pairing key. Remove the native-host registration and unpacked extension separately if they are no longer needed.

The extension's manifest version must track the Chimera package version. For this release it is `0.72.0`; update `manifest.json` and `extensions/chrome/README.md` together when the package version changes. The package-generation scripts do not upload or publish anything. Store submission and publication are owner-only actions.

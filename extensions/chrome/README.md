# Chimera Chrome extension (E0 read-only foundation)

1. Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**, and select this directory. Copy the extension ID shown on the card.
2. From the repository root, run `python scripts/register_chrome_host.py <32-character-extension-id>` to print the registration plan. `--apply` writes the Native Messaging manifest and the `HKCU\Software\Google\Chrome\NativeMessagingHosts\app.chimera.browser` registry value. The host executable must already be installed at the `--executable` path.

This is an E0 protocol foundation, not a paired product: the native host deliberately refuses until an authenticated app pairing/forwarding channel is implemented. Site access is optional and must be granted explicitly. Only reading and navigation operations are defined; click/type are unavailable until E2. The Stop button detaches the debugger and closes the Chimera-owned tab group.

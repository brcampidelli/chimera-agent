/** Which shell is showing this page.
 *
 *  The same React build is served two ways: inside the Tauri bundle a user installed, and from
 *  `chimera app` in an ordinary browser. Almost nothing cares — but update advice does, and it was
 *  written for only one of them.
 *
 *  The installed bundle carries a complete signed updater: `tauri-plugin-updater` checks GitHub at
 *  launch, verifies the signature against the embedded pubkey, asks, installs and restarts. The
 *  badge told that user "There's no in-place auto-update yet" and handed them
 *  `pip install -U 'chimera-agent[desktop]'` — which updates the Python package, not the app they
 *  are looking at. In a browser the same command is exactly right.
 *
 *  Probed per call rather than cached at module load. The shell genuinely cannot change under a
 *  running page, so caching would be correct — but it would also make this only testable by
 *  resetting the module registry, and a fresh registry hands the component a different i18n module
 *  from the one the test's provider set up. Two property checks are not worth that.
 */
export function isNativeShell(): boolean {
  return (
    typeof window !== "undefined" &&
    ("__TAURI_INTERNALS__" in window || "__TAURI__" in window)
  );
}

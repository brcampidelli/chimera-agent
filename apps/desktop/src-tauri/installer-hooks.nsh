; Installer hooks for the Windows (NSIS) build.
;
; Why this file exists: installing over a running Chimera failed with a wall of
;
;   Error opening file for writing:
;   …\sidecar-dist\chimera-backend\_internal\MSVCP140.dll
;
; one dialog per locked DLL, with Abort / Retry / Ignore and no explanation. Windows will not
; overwrite a file that a running process has open, and the process holding those files is not the
; one the installer knows about: the app is `chimera-desktop.exe`, but the DLLs belong to
; `chimera-backend.exe`, the frozen Python sidecar it launches as a child. NSIS's own "close the
; application" step never sees it.
;
; Killing BOTH is deliberate, and the order matters. The shell supervises its sidecar and restarts it
; if it dies, so stopping only the backend would have the app spawn a replacement mid-install and
; lock the same files again. The shell goes first, then the backend that may have outlived it.
;
; Nothing is lost by this. Conversations are written to disk as each turn completes, settings are
; written when saved, and the sidecar keeps no state of its own — so a killed Chimera reopens on the
; same conversations. What it replaces is worse than abrupt: a stack of error dialogs about a file
; nobody outside this project can place, on the one screen where a user has no way to recover.

!macro NSIS_HOOK_PREINSTALL
  DetailPrint "Closing Chimera before installing…"
  ; /T takes the process tree with it — the shell owns the sidecar, and a detached child is exactly
  ; the case that produced this bug. Failure is ignored on purpose: "not running" is the normal
  ; case, and taskkill reports it as an error code.
  nsExec::Exec 'taskkill /F /T /IM chimera-desktop.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM chimera-backend.exe'
  Pop $0
  ; Windows releases file handles asynchronously: the process is gone before its locks are. Without
  ; this pause the very next step can still meet a locked DLL, which is the failure being fixed.
  Sleep 1500

  ; --- and then the directory goes, rather than being installed over.
  ;
  ; The frozen sidecar is a PyInstaller bundle: a pile of files whose NAMES carry versions. NSIS
  ; overwrites what it is given and leaves everything else, so upgrading MERGED the two releases —
  ; and `_internal` ended up holding both `chimera_agent-0.48.0.dist-info` and
  ; `chimera_agent-0.49.0.dist-info`. `importlib.metadata.version("chimera-agent")` answers with the
  ; first one it finds, so a freshly updated 0.49.0 app reported 0.48.0 of itself.
  ;
  ; What that broke, measured on a real machine after a real update: `/api/version` compared the
  ; stale version against GitHub and kept saying "v0.49.0 available" on an app that WAS 0.49.0. The
  ; badge never goes away, and the next launch offers the update again. Anything else keyed on the
  ; version — receipts, telemetry, the update check itself — was reading the previous release.
  ;
  ; **CI cannot catch this**, and that is why it shipped. The pipeline's "the frozen sidecar knows
  ; its own version" step builds into an empty tree, where there is exactly one dist-info; the defect
  ; needs a PREVIOUS install to merge with. An instrument that cannot exhibit an effect produces no
  ; evidence about it. Nothing here proves the upgrade path except an actual upgrade.
  ;
  ; Safe to wipe: everything under `sidecar-dist` is build output, replaced wholesale by this
  ; installer. User data lives in the app data directory, which this does not touch.
  DetailPrint "Removing the previous sidecar bundle…"
  RMDir /r "$INSTDIR\sidecar-dist"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  ; Same problem, same fix: uninstalling with the app open leaves the sidecar's directory behind and
  ; reports a removal that did not fully happen.
  DetailPrint "Closing Chimera before uninstalling…"
  nsExec::Exec 'taskkill /F /T /IM chimera-desktop.exe'
  Pop $0
  nsExec::Exec 'taskkill /F /T /IM chimera-backend.exe'
  Pop $0
  Sleep 1500
!macroend

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

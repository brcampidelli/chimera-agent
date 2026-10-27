const status = document.getElementById('status');
const allow = document.getElementById('allow');
const stop = document.getElementById('stop');
const pair = document.getElementById('pair');
const pairCode = document.getElementById('pairCode');
const pairStatus = document.getElementById('pairStatus');
const note = document.getElementById('note');
const canceled = document.getElementById('canceled');
const resume = document.getElementById('resume');

async function refresh() {
  const value = await chrome.runtime.sendMessage({ op: 'status' });
  status.textContent = value.status;
  allow.hidden = !value.requestedOrigin;
  allow.dataset.origin = value.requestedOrigin;
  allow.textContent = value.requestedOrigin ? `Allow ${value.requestedOrigin}` : 'Allow site';
  // Cancel is an owner kill-switch: the note and the Resume button show only while it is set.
  canceled.hidden = !value.actionsDisabled;
  resume.hidden = !value.actionsDisabled;
}

function showNote(text) {
  note.textContent = text;
  note.hidden = text === '';
}

refresh();
allow.addEventListener('click', async () => {
  const origin = allow.dataset.origin;
  if (!origin) return;
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  // The worker drops requestedOrigin once the origin is granted, so the refresh below hides Allow.
  showNote(granted ? `Allowed ${origin}` : 'Site permission denied');
  await refresh();
});
resume.addEventListener('click', async () => {
  resume.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ op: 'resume' });
    showNote(result?.ok ? 'Chimera actions resumed' : 'Could not resume Chimera actions');
  } catch {
    showNote('Could not resume Chimera actions');
  } finally { resume.disabled = false; }
  await refresh();
});
pair.addEventListener('click', async () => {
  const code = pairCode.value.trim();
  if (!/^\d{6}$/.test(code)) { pairStatus.textContent = 'Enter the six-digit code shown in Chimera Desktop.'; return; }
  pair.disabled = true;
  pairStatus.textContent = 'Pairing…';
  try {
    const result = await chrome.runtime.sendMessage({ op: 'pair', code });
    if (!result?.ok) throw new Error('Pairing failed');
    pairStatus.textContent = 'Paired with Chimera Desktop.';
  } catch {
    pairStatus.textContent = 'Could not pair. Check the code and try again.';
  } finally { pair.disabled = false; }
});
stop.addEventListener('click', async () => {
  status.textContent = 'Stopping…';
  const result = await chrome.runtime.sendMessage({ op: 'stop' });
  status.textContent = result.error || 'Stopped';
});

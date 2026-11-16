const status = document.getElementById('status');
const allow = document.getElementById('allow');
const stop = document.getElementById('stop');
const start = document.getElementById('start');
const stoppedNote = document.getElementById('stoppedNote');
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
  // Stop holds across worker restarts and Chrome restarts; this reflects what is stored, not what was clicked.
  stop.hidden = value.stopped === true;
  start.hidden = value.stopped !== true;
  stoppedNote.hidden = value.stopped !== true;
  if (value.issues && value.issues.length) showNote(`Protection problem: ${value.issues.join('; ')}`);
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
  await refresh(); // a code entered here also ends a Stop
});
stop.addEventListener('click', async () => {
  status.textContent = 'Stopping…';
  const result = await chrome.runtime.sendMessage({ op: 'stop' });
  if (result.error) { status.textContent = result.error; return; }
  await refresh();
  // Anything the teardown could not do cleanly is said, not hidden.
  if (result.issues && result.issues.length) showNote(`Stopped, with problems: ${result.issues.join('; ')}`);
});
start.addEventListener('click', async () => {
  start.disabled = true;
  try {
    const result = await chrome.runtime.sendMessage({ op: 'start' });
    showNote(result?.ok ? 'Chimera can use the browser again' : 'Could not start Chimera Browser');
  } catch {
    showNote('Could not start Chimera Browser');
  } finally { start.disabled = false; }
  await refresh();
});

const status = document.getElementById('status');
const allow = document.getElementById('allow');
const stop = document.getElementById('stop');
const pair = document.getElementById('pair');
const pairCode = document.getElementById('pairCode');
const pairStatus = document.getElementById('pairStatus');

async function refresh() {
  const value = await chrome.runtime.sendMessage({ op: 'status' });
  status.textContent = value.status;
  allow.hidden = !value.requestedOrigin;
  allow.dataset.origin = value.requestedOrigin;
  allow.textContent = value.requestedOrigin ? `Allow ${value.requestedOrigin}` : 'Allow site';
}

refresh();
allow.addEventListener('click', async () => {
  const origin = allow.dataset.origin;
  if (!origin) return;
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  status.textContent = granted ? 'Site permission granted' : 'Site permission denied';
  await refresh();
});
pair.addEventListener('click', async () => {
  const code = pairCode.value.trim();
  if (!/^\\d{6}$/.test(code)) { pairStatus.textContent = 'Enter the six-digit code shown in Chimera Desktop.'; return; }
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

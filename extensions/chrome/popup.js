const status = document.getElementById('status');
const allow = document.getElementById('allow');
const stop = document.getElementById('stop');

async function refresh() {
  const value = await chrome.runtime.sendMessage({ op: 'status' });
  status.textContent = value.status;
  allow.hidden = !value.requestedOrigin;
  allow.dataset.origin = value.requestedOrigin;
}

refresh();
allow.addEventListener('click', async () => {
  const origin = allow.dataset.origin;
  if (!origin) return;
  const granted = await chrome.permissions.request({ origins: [`${origin}/*`] });
  status.textContent = granted ? 'Site permission granted' : 'Site permission denied';
  await refresh();
});
stop.addEventListener('click', async () => {
  status.textContent = 'Stopping…';
  const result = await chrome.runtime.sendMessage({ op: 'stop' });
  status.textContent = result.error || 'Stopped';
});

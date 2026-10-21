const HOST = 'app.chimera.browser';
const CDP = '1.3';
let nativePort;
let groupId;
let activeTabId;
let nextId = 1;
let requestedOrigin;
const tabIds = new Set();

async function persistState() {
  await chrome.storage.session.set({
    chimeraGroupId: groupId,
    chimeraTabIds: [...tabIds],
    chimeraActiveTabId: activeTabId,
    chimeraRequestedOrigin: requestedOrigin,
  });
}

async function restoreState() {
  const saved = await chrome.storage.session.get([
    'chimeraGroupId', 'chimeraTabIds', 'chimeraActiveTabId', 'chimeraRequestedOrigin',
  ]);
  groupId = saved.chimeraGroupId;
  tabIds.clear();
  for (const tabId of saved.chimeraTabIds || []) tabIds.add(tabId);
  activeTabId = saved.chimeraActiveTabId;
  requestedOrigin = saved.chimeraRequestedOrigin;
}

function setStatus(text) {
  chrome.storage.session.set({ chimeraStatus: text });
}

async function ensureGroup() {
  if (groupId !== undefined) {
    try {
      await chrome.tabGroups.get(groupId);
      const tab = activeTabId === undefined ? null : await chrome.tabs.get(activeTabId).catch(() => null);
      if (tab && tab.groupId === groupId) return;
    } catch { /* the owner closed our group */ }
    groupId = undefined;
    activeTabId = undefined;
  }
  const tab = await chrome.tabs.create({ active: true });
  tabIds.add(tab.id);
  activeTabId = tab.id;
  groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await chrome.tabGroups.update(groupId, { title: 'Chimera', collapsed: false });
  await persistState();
}

async function attach(tabId) {
  try { await chrome.debugger.attach({ tabId }, CDP); }
  catch (error) {
    if (!String(error).includes('Another debugger is already attached')) throw error;
  }
}

async function cdp(method, params = {}) {
  if (activeTabId === undefined) throw new Error('No Chimera tab is active');
  return chrome.debugger.sendCommand({ tabId: activeTabId }, method, params);
}

async function elements() {
  const tree = await cdp('Accessibility.getFullAXTree');
  return { nodes: tree.nodes.map(node => ({
    role: node.role, name: node.name, properties: node.properties,
    backendDOMNodeId: node.backendDOMNodeId,
  })) };
}

async function checkCurrentOrigin() {
  if (activeTabId === undefined || groupId === undefined) throw new Error('No Chimera tab is active');
  const tab = await chrome.tabs.get(activeTabId);
  if (tab.groupId !== groupId) throw new Error('Active tab is outside the Chimera group');
  let url;
  try { url = new URL(tab.url); } catch { throw new Error('Current page has no permitted web origin'); }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') throw new Error('Current page has no permitted web origin');
  const origin = url.origin;
  if (!await chrome.permissions.contains({ origins: [`${origin}/*`] })) {
    requestedOrigin = origin;
    await persistState();
    throw new Error('This site has not been granted to the Chimera extension');
  }
  requestedOrigin = undefined;
  await persistState();
}

async function waitForLoad() {
  await cdp('Page.enable');
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      chrome.debugger.onEvent.removeListener(listener);
      reject(new Error('Page load timed out'));
    }, 15000);
    const listener = (source, method) => {
      if (source.tabId !== activeTabId || method !== 'Page.loadEventFired') return;
      clearTimeout(timeout);
      chrome.debugger.onEvent.removeListener(listener);
      resolve();
    };
    chrome.debugger.onEvent.addListener(listener);
  });
}

async function handle(message) {
  if (!message || typeof message.id !== 'number' || typeof message.op !== 'string')
    throw new Error('Invalid request');
  await restoreState();
  await ensureGroup();
  await attach(activeTabId);
  switch (message.op) {
    case 'navigate': {
      if (typeof message.url !== 'string' || !/^https?:\/\//i.test(message.url)) throw new Error('Only http(s) navigation is supported');
      let requested;
      try { requested = new URL(message.url); } catch { throw new Error('Invalid navigation URL'); }
      if (!await chrome.permissions.contains({ origins: [`${requested.origin}/*`] })) {
        requestedOrigin = requested.origin;
        await persistState();
        throw new Error('This site has not been granted to the Chimera extension');
      }
      await cdp('Page.enable');
      const loading = waitForLoad();
      const result = await cdp('Page.navigate', { url: message.url });
      if (result.errorText) throw new Error('Navigation failed');
      await loading;
      await checkCurrentOrigin();
      return await elements();
    }
    case 'read':
      await checkCurrentOrigin();
      return await elements();
    case 'read_text': {
      await checkCurrentOrigin();
      const result = await cdp('Runtime.evaluate', { expression: 'document.body ? document.body.innerText : ""', returnByValue: true });
      return result.result.value || '';
    }
    case 'back': {
      await checkCurrentOrigin();
      const history = await cdp('Page.getNavigationHistory');
      if (history.currentIndex > 0) {
        const loading = waitForLoad();
        await cdp('Page.navigateToHistoryEntry', { entryId: history.entries[history.currentIndex - 1].id });
        await loading;
      }
      await checkCurrentOrigin();
      return await elements();
    }
    case 'screenshot': {
      await checkCurrentOrigin();
      const capture = await cdp('Page.captureScreenshot', { format: 'jpeg', quality: 60 });
      return capture.data;
    }
    case 'close': await stop(); return true;
    default: throw new Error('Unsupported operation');
  }
}

function connectNative() {
  if (nativePort) return;
  nativePort = chrome.runtime.connectNative(HOST);
  nativePort.onMessage.addListener(async message => {
    if (!message || typeof message.op !== 'string') return;
    try {
      const result = await handle(message);
      nativePort?.postMessage({ id: message.id, ok: true, result });
    } catch (error) {
      nativePort?.postMessage({ id: message.id, ok: false, error: String(error) });
    }
  });
  nativePort.onDisconnect.addListener(() => {
    nativePort = undefined;
    setStatus('Disconnected');
  });
  setStatus('Connected');
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.op === 'status') {
    chrome.storage.session.get(['chimeraStatus', 'chimeraRequestedOrigin']).then(value => sendResponse({
      status: value.chimeraStatus || 'Stopped', requestedOrigin: value.chimeraRequestedOrigin || '',
    }));
    return true;
  }
  if (request.op === 'stop') {
    stop().then(() => sendResponse({ ok: true })).catch(() => sendResponse({ error: 'Could not stop Chimera browser' }));
    return true;
  }
  return false;
});

async function stop() {
  await restoreState();
  for (const tabId of tabIds) {
    try { await chrome.debugger.detach({ tabId }); } catch { /* tab already closed or detached */ }
  }
  const ownedTabs = [...tabIds];
  if (ownedTabs.length) {
    try { await chrome.tabs.remove(ownedTabs); } catch { /* tabs were already closed */ }
  }
  tabIds.clear();
  groupId = undefined;
  activeTabId = undefined;
  requestedOrigin = undefined;
  await persistState();
  if (nativePort) { nativePort.disconnect(); nativePort = undefined; }
  setStatus('Stopped');
}

chrome.debugger.onDetach.addListener(source => {
  if (source.tabId === activeTabId) setStatus('Debugger detached');
});

chrome.runtime.onStartup.addListener(connectNative);
chrome.runtime.onInstalled.addListener(connectNative);
restoreState().then(connectNative);
connectNative();

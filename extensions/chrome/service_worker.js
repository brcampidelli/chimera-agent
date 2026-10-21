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

async function describeTarget(refNode) {
  if (!Number.isInteger(refNode) || refNode < 1) throw new Error('Invalid target node');
  const described = await cdp('DOM.describeNode', { backendNodeId: refNode });
  const targetNode = described.node;
  if (!targetNode || !targetNode.nodeName) throw new Error('Target node is unavailable');
  const document = await cdp('DOM.getDocument', { depth: -1, pierce: true });
  let node;
  const parents = [];
  function seek(candidate, lineage) {
    if (candidate.backendNodeId === refNode) {
      node = candidate;
      parents.push(...lineage);
      return true;
    }
    const next = [...lineage, candidate];
    for (const child of [...(candidate.children || []), ...(candidate.shadowRoots || [])])
      if (seek(child, next)) return true;
    if (candidate.contentDocument && seek(candidate.contentDocument, next)) return true;
    return false;
  }
  seek(document.root, []);
  node = node || targetNode;
  const attrs = {};
  for (let index = 0; index < (node.attributes || []).length; index += 2)
    attrs[node.attributes[index].toLowerCase()] = node.attributes[index + 1];
  const attrsOf = candidate => {
    const attrs = {};
    for (let index = 0; index < (candidate.attributes || []).length; index += 2)
      attrs[candidate.attributes[index].toLowerCase()] = candidate.attributes[index + 1];
    return attrs;
  };
  const ancestors = parents.slice(-8).map(parent => ({
    tag: String(parent.nodeName || '').toLowerCase(), ...attrsOf(parent),
  }));
  const frameTree = await cdp('Page.getFrameTree');
  const frameUrls = [];
  function collectFrames(frame) {
    if (frame && frame.frame && frame.frame.url) frameUrls.push(frame.frame.url);
    for (const child of (frame && frame.childFrames) || []) collectFrames(child);
  }
  collectFrames(frameTree.frameTree);
  const frame = [...frameUrls, ...ancestors.map(item => `${item.src || ''} ${item.title || ''} ${item.name || ''}`)].join(' ');
  const label = ancestors.find(item => item.tag === 'label');
  const form = ancestors.find(item => item.tag === 'form');
  const tag = String(node.nodeName).toLowerCase();
  const formNode = parents.find(parent => String(parent.nodeName || '').toLowerCase() === 'FORM');
  const formAttrs = formNode ? attrsOf(formNode) : {};
  const description = {
    tag,
    type: attrs.type || '',
    autocomplete: attrs.autocomplete || '',
    name: attrs.name || '',
    id: attrs.id || '',
    ariaLabel: attrs['aria-label'] || '',
    role: attrs.role || '',
    label: label ? attrsOf(label).value || attrsOf(label)['aria-label'] || '' : '',
    accessibleName: attrs['aria-label'] || attrs.alt || attrs.title || attrs.value || '',
    text: node.nodeValue || node.textContent || '',
    value: attrs.value || '',
    href: attrs.href || '',
    download: Object.prototype.hasOwnProperty.call(attrs, 'download'),
    submitsForm: Boolean(form) && (tag === 'button' || (tag === 'input' && (attrs.type || 'text') === 'submit')),
    formAction: formAttrs.action || '',
    frame,
  };
  return description;
}

function protectedTarget(target) {
  const autocompleteTokens = String(target.autocomplete || '').toLowerCase().split(/\s+/);
  const autocomplete = autocompleteTokens[0] || '';
  const identity = `${target.name || ''} ${target.id || ''} ${target.label || ''} ${target['aria-label'] || ''}`;
  return (target.tag === 'input' && String(target.type).toLowerCase() === 'password')
    || autocomplete.startsWith('cc-')
    || ['one-time-code', 'current-password', 'new-password'].includes(autocomplete)
    || /password|senha|cvv|cvc|card\\s*number|pin|token|2fa|verification/i.test(identity);
}

async function actOnTarget(operation, refNode, text = '') {
  await checkCurrentOrigin();
  const target = await describeTarget(refNode);
  if (protectedTarget(target)) throw new Error('Sensitive field is blocked by the extension');
  if (operation === 'click') {
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    if (!resolved.object || !resolved.object.objectId) throw new Error('Target node is unavailable');
    const model = await cdp('DOM.getBoxModel', { objectId: resolved.object.objectId });
    const quad = model.model && model.model.content;
    if (!quad || quad.length < 8) throw new Error('Target is not clickable');
    const x = (quad[0] + quad[2] + quad[4] + quad[6]) / 4;
    const y = (quad[1] + quad[3] + quad[5] + quad[7]) / 4;
    await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  } else {
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    if (!resolved.object || !resolved.object.objectId) throw new Error('Target node is unavailable');
    await cdp('DOM.focus', { backendNodeId: refNode });
    await cdp('Input.insertText', { text });
  }
  return { target, elements: await elements() };
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
    case 'describe':
      await checkCurrentOrigin();
      return await describeTarget(message.ref_node);
    case 'click':
      return await actOnTarget('click', message.ref_node);
    case 'type':
      if (typeof message.text !== 'string') throw new Error('Invalid text');
      return await actOnTarget('type', message.ref_node, message.text);
    case 'current_url': {
      await checkCurrentOrigin();
      const tab = await chrome.tabs.get(activeTabId);
      return tab.url || '';
    }
    case 'google_results': {
      await checkCurrentOrigin();
      const result = await cdp('Runtime.evaluate', {
        expression: `Array.from(document.querySelectorAll('div.MjjYud')).slice(0, 10).map(node => {
          const link = node.querySelector('a');
          const title = node.querySelector('h3');
          const snippet = node.querySelector('[data-sncf], .VwiC3b');
          return { title: title?.innerText || '', link: link?.href || '', snippet: snippet?.innerText || '' };
        })`,
        returnByValue: true,
      });
      return result.result.value || [];
    }
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

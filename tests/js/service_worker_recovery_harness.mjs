// Recovery paths of the extension, run against the real service_worker.js and popup.js in a vm:
// a refused Page.setDownloadBehavior must not break actions, the download guard cancels what it must,
// a cancel from Chrome's debugging bar stays until the owner resumes it from the popup, and a granted
// origin is no longer offered for "Allow". Run through tests/test_browser_chrome_e2.py.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const workerSource = fs.readFileSync(path.join(root, 'extensions/chrome/service_worker.js'), 'utf8');
const popupSource = fs.readFileSync(path.join(root, 'extensions/chrome/popup.js'), 'utf8');
const popupHtml = fs.readFileSync(path.join(root, 'extensions/chrome/popup.html'), 'utf8');

const GROUP = 7;
const SHOP = 'https://shop.example';
const BROWSER_LEVEL = 'Cannot not access browser-level commands';

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

async function openBrowser({ denyError = null, granted = [SHOP] } = {}) {
  const grantedOrigins = new Set(granted);
  let store = { chimeraGroupId: GROUP, chimeraTabIds: [1], chimeraActiveTabId: 1 };
  const tabs = new Map([[1, { id: 1, groupId: GROUP, url: `${SHOP}/` }]]);
  const groupsOpen = new Set([GROUP]);
  let nextTabId = 2;
  const pending = new Map();
  let requestId = 0;
  let nativeListener;
  let workerOnMessage;
  let downloadListener;
  let detachListener;
  let loadListeners = [];
  const log = { attached: [], cancelled: [], erased: [] };
  const hooks = { duringRead: null };

  const chrome = {
    storage: { session: {
      async get() { return store; },
      async set(value) { store = { ...store, ...value }; },
    } },
    tabs: {
      async get(id) {
        const tab = tabs.get(id);
        if (!tab) throw new Error(`No tab ${id}`);
        return tab;
      },
      async create({ url, active }) {
        const tab = { id: nextTabId++, url: url || 'chrome://newtab/', groupId: undefined, active };
        tabs.set(tab.id, tab);
        return tab;
      },
      async group({ tabIds, groupId }) {
        const id = groupId ?? GROUP;
        for (const tabId of tabIds) tabs.get(tabId).groupId = id;
        return id;
      },
      async remove() {},
      async query({ groupId }) { return [...tabs.values()].filter(tab => tab.groupId === groupId); },
    },
    tabGroups: {
      async get(id) { if (!groupsOpen.has(id)) throw new Error('No group'); return { id }; },
      async update() {},
    },
    permissions: {
      async contains({ origins }) { return origins.every(origin => grantedOrigins.has(origin.replace(/\/\*$/, ''))); },
      async request({ origins }) {
        for (const origin of origins) grantedOrigins.add(origin.replace(/\/\*$/, ''));
        return true;
      },
    },
    downloads: {
      onCreated: { addListener(fn) { downloadListener = fn; } },
      async cancel(id) { log.cancelled.push(id); },
      async erase({ id }) { log.erased.push(id); },
    },
    debugger: {
      async attach({ tabId }) { log.attached.push(tabId); },
      async detach() {},
      onEvent: {
        addListener(fn) { loadListeners.push(fn); },
        removeListener(fn) { loadListeners = loadListeners.filter(item => item !== fn); },
      },
      onDetach: { addListener(fn) { detachListener = fn; } },
      async sendCommand({ tabId }, method, params = {}) {
        if (method === 'Page.setDownloadBehavior') {
          if (denyError === null) return {};
          // Shaped like the rejection Chrome returns today: code -32000 with this message.
          const error = new Error(denyError);
          error.code = -32000;
          throw error;
        }
        if (method === 'Page.enable') return {};
        if (method === 'Page.navigate') {
          tabs.get(tabId).url = params.url;
          setImmediate(() => loadListeners.slice().forEach(fn => fn({ tabId }, 'Page.loadEventFired')));
          return {};
        }
        if (method === 'Accessibility.getFullAXTree') {
          if (hooks.duringRead) hooks.duringRead();
          return { nodes: [] };
        }
        throw new Error(`Unexpected CDP method ${method}`);
      },
    },
    runtime: {
      connectNative() {
        return {
          onMessage: { addListener(fn) { nativeListener = fn; } },
          onDisconnect: { addListener() {} },
          postMessage(reply) { pending.get(reply.id)?.(reply); pending.delete(reply.id); },
          disconnect() {},
        };
      },
      onMessage: { addListener(fn) { workerOnMessage = fn; } },
      onStartup: { addListener() {} },
      onInstalled: { addListener() {} },
    },
  };

  vm.runInContext(workerSource, vm.createContext({ chrome, URL, setTimeout, clearTimeout, console }), { filename: 'service_worker.js' });
  await flush();
  assert.equal(typeof nativeListener, 'function', 'native listener installed');
  assert.equal(typeof workerOnMessage, 'function', 'popup message listener installed');

  return {
    chrome, log, hooks,
    store: () => store,
    ask(op, extra = {}) {
      return new Promise(resolve => {
        const id = ++requestId;
        pending.set(id, resolve);
        nativeListener({ id, op, ...extra });
      });
    },
    sendToWorker(message) {
      return new Promise(resolve => {
        if (workerOnMessage(message, {}, resolve) !== true) resolve(undefined);
      });
    },
    fireDownload(item) { downloadListener(item); },
    fireDetach(source, reason) { detachListener(source, reason); },
  };
}

// The popup runs against the same markup: every element id in popup.html becomes a fake element.
function openPopup(browser) {
  const elements = new Map();
  for (const match of popupHtml.matchAll(/\sid="([^"]+)"/g)) {
    elements.set(match[1], {
      hidden: false, disabled: false, textContent: '', value: '', dataset: {}, listeners: {},
      addEventListener(type, fn) { this.listeners[type] = fn; },
      async click() { await this.listeners.click(); },
    });
  }
  const document = {
    getElementById(id) {
      if (!elements.has(id)) throw new Error(`popup has no #${id}`);
      return elements.get(id);
    },
  };
  const chrome = {
    runtime: { sendMessage: message => browser.sendToWorker(message) },
    permissions: browser.chrome.permissions,
  };
  vm.runInContext(popupSource, vm.createContext({ document, chrome, console }), { filename: 'popup.js' });
  return elements;
}

// 1. Chrome refuses Page.setDownloadBehavior: navigate and read still succeed, and the attach still happens.
{
  const browser = await openBrowser({ denyError: BROWSER_LEVEL });
  const navigated = await browser.ask('navigate', { url: `${SHOP}/catalog` });
  assert.equal(navigated.ok, true, `navigate survives the refused denial: ${JSON.stringify(navigated)}`);
  const read = await browser.ask('read');
  assert.equal(read.ok, true, `read survives the refused denial: ${JSON.stringify(read)}`);
  assert.ok(browser.log.attached.length >= 1, 'the debugger still attaches');
}

// 2. Only the browser-level refusal is swallowed: any other failure of the same command still fails the action.
{
  const browser = await openBrowser({ denyError: 'Some other failure' });
  const read = await browser.ask('read');
  assert.equal(read.ok, false, 'an unrelated denial error is not swallowed');
  assert.match(read.error, /Some other failure/);
}

// 3. The download guard: idle owner downloads elsewhere pass; a download from the Chimera tab's origin is
//    cancelled and erased; a download that starts during an action is cancelled whatever its origin.
{
  const browser = await openBrowser();
  browser.fireDownload({ id: 10, url: 'https://files.example/report.pdf', referrer: 'https://files.example/' });
  await flush();
  assert.deepEqual(browser.log.cancelled, [], 'an owner download elsewhere is left alone');

  browser.fireDownload({ id: 11, url: `${SHOP}/invoice.pdf`, referrer: `${SHOP}/` });
  await flush();
  assert.deepEqual(browser.log.cancelled, [11], 'a download from the Chimera tab origin is cancelled');
  assert.deepEqual(browser.log.erased, [11], 'and erased from history');

  browser.hooks.duringRead = () => browser.fireDownload({ id: 12, url: 'https://evil.example/payload.exe', referrer: '' });
  const read = await browser.ask('read');
  browser.hooks.duringRead = null;
  await flush();
  assert.equal(read.ok, true, JSON.stringify(read));
  assert.ok(browser.log.cancelled.includes(12), 'a download started during a browser action is cancelled');
}

// 4. Cancel on Chrome's debugging bar sets the kill-switch: actions are refused with the owner message, and
//    an app request cannot clear it.
{
  const browser = await openBrowser();
  const first = await browser.ask('navigate', { url: `${SHOP}/catalog` });
  assert.equal(first.ok, true, JSON.stringify(first));
  browser.fireDetach({ tabId: 1 }, 'canceled_by_user');
  await flush();
  await flush();
  assert.equal(browser.store().chimeraActionsDisabled, true);
  const blocked = await browser.ask('navigate', { url: `${SHOP}/shop` });
  assert.equal(blocked.ok, false);
  assert.match(blocked.error, /Resume Chimera actions/, 'the refusal tells the owner where to resume');
  const again = await browser.ask('read');
  assert.equal(again.ok, false, 'a repeated app request does not clear the kill-switch');
}

// 5. The owner clicks Resume in the popup: the flag is cleared in storage and the worker, and navigate works.
{
  const browser = await openBrowser();
  await browser.ask('navigate', { url: `${SHOP}/catalog` });
  browser.fireDetach({ tabId: 1 }, 'canceled_by_user');
  await flush();
  await flush();
  const popup = openPopup(browser);
  await flush();
  assert.equal(popup.get('canceled').hidden, false, 'the popup explains the cancel');
  assert.equal(popup.get('resume').hidden, false, 'the popup offers Resume');
  await popup.get('resume').click();
  await flush();
  assert.equal(browser.store().chimeraActionsDisabled, false, 'storage flag cleared by the owner');
  assert.equal(popup.get('resume').hidden, true);
  assert.equal(popup.get('note').textContent, 'Chimera actions resumed');
  const navigated = await browser.ask('navigate', { url: `${SHOP}/shop` });
  assert.equal(navigated.ok, true, `navigate succeeds after resume: ${JSON.stringify(navigated)}`);
}

// 6. A site granted through the popup is no longer offered for Allow, and its requested origin is cleared.
{
  const browser = await openBrowser({ granted: [] });
  const refused = await browser.ask('navigate', { url: `${SHOP}/catalog` });
  assert.equal(refused.ok, false);
  assert.match(refused.error, /not been granted/);
  assert.equal(browser.store().chimeraRequestedOrigin, SHOP, 'the worker remembers the origin the owner must allow');

  const popup = openPopup(browser);
  await flush();
  assert.equal(popup.get('allow').hidden, false, 'Allow is offered for the requested origin');
  assert.equal(popup.get('allow').dataset.origin, SHOP);
  await popup.get('allow').click();
  await flush();
  assert.equal(popup.get('allow').hidden, true, 'Allow is hidden once the origin is granted');
  assert.equal(popup.get('note').textContent, `Allowed ${SHOP}`);
  assert.equal(browser.store().chimeraRequestedOrigin, undefined, 'requested origin cleared in storage');
  const read = await browser.ask('read');
  assert.equal(read.ok, true, JSON.stringify(read));
}

console.log('service worker recovery cases: 6');

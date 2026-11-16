// Recovery paths of the extension, run against the real service_worker.js and popup.js in a vm:
// a refused Page.setDownloadBehavior must not break actions, the download guard cancels what it must,
// a cancel from Chrome's debugging bar stays until the owner resumes it from the popup, and a granted
// origin is no longer offered for "Allow". It also pins the audit's three fixes (ext-02: a navigation is held
// to the grant on its DESTINATION, redirects and history included; ext-04: Stop holds across a restart of the
// worker and of Chrome; ext-08: so does the debugging bar's Cancel). Run through tests/test_browser_chrome_e2.py.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { assertValidFetchFilter } from './fetch_filter.mjs';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const workerSource = fs.readFileSync(path.join(root, 'extensions/chrome/service_worker.js'), 'utf8');
const popupSource = fs.readFileSync(path.join(root, 'extensions/chrome/popup.js'), 'utf8');
const popupHtml = fs.readFileSync(path.join(root, 'extensions/chrome/popup.html'), 'utf8');

const GROUP = 7;
const SHOP = 'https://shop.example';
const BROWSER_LEVEL = 'Cannot not access browser-level commands';

// Objects built inside the vm belong to another realm; compare them as plain data.
const plain = value => JSON.parse(JSON.stringify(value));

function flush() {
  return new Promise(resolve => setImmediate(resolve));
}

async function openBrowser({ denyError = null, granted = [SHOP], initialStore = null, initialLocal = {}, expectConnected = true, noTabs = false } = {}) {
  const grantedOrigins = new Set(granted);
  let store = initialStore || (noTabs ? {} : { chimeraGroupId: GROUP, chimeraTabIds: [1], chimeraActiveTabId: 1 });
  let local = { ...initialLocal };
  const tabs = new Map(noTabs ? [] : [[1, { id: 1, groupId: GROUP, url: `${SHOP}/` }]]);
  const groupsOpen = new Set(noTabs ? [] : [GROUP]);
  let nextTabId = 2;
  const pending = new Map();
  let requestId = 0;
  let nativeListener;
  let workerOnMessage;
  let downloadListener;
  let detachListener;
  let loadListeners = [];
  const log = { attached: [], cancelled: [], erased: [], continued: [], failed: [], connects: 0, navigations: [], requests: [], created: [], removed: [], detached: [], cdp: [], order: [], unprotectedLoads: [], bypassIgnored: [] };
  const networkOn = new Set();
  let tabsCreatedListener;
  let tabsUpdatedListener;
  const verdicts = new Map();
  let fetchId = 0;
  let startupListener;
  let installedListener;
  const hooks = {
    duringRead: null, redirects: {}, history: { currentIndex: 0, entries: [] },
    permissionGate: null, failRequestError: null, removeFails: false, blankNavigateResult: null, failSession: {}, prerenderMissing: false,
  };

  // What Chrome does with a main-frame navigation: each hop (the request, then every redirect) pauses at
  // Fetch.requestPaused until the extension continues or fails it. A failed hop ends the navigation.
  function navigateThrough(tabId, urls) {
    setImmediate(async () => {
      for (const hop of urls) {
        const requestId = `req-${++fetchId}`;
        const verdict = new Promise(resolve => verdicts.set(requestId, resolve));
        log.requests.push({ requestId, url: hop });
        loadListeners.slice().forEach(fn => fn({ tabId }, 'Fetch.requestPaused', {
          requestId, frameId: 'main', resourceType: 'Document', request: { url: hop },
        }));
        if (await verdict === 'failed') return;
      }
      tabs.get(tabId).url = urls[urls.length - 1];
      loadListeners.slice().forEach(fn => fn({ tabId }, 'Page.loadEventFired'));
    });
  }

  const chrome = {
    storage: {
      session: {
        async get() { return store; },
        async set(value) { store = { ...store, ...value }; },
      },
      local: {
        async get() { return local; },
        async set(value) { local = { ...local, ...value }; },
      },
    },
    tabs: {
      async get(id) {
        const tab = tabs.get(id);
        if (!tab) throw new Error(`No tab ${id}`);
        return tab;
      },
      async create({ url, active }) {
        const tab = { id: nextTabId++, url: url || 'chrome://newtab/', groupId: undefined, active };
        tabs.set(tab.id, tab);
        log.created.push(url);
        log.order.push(`tabs.create ${url}`);
        // A tab created on a real URL starts loading it at once, before any debugger can be attached.
        if (url && url !== 'about:blank') log.unprotectedLoads.push(url);
        return tab;
      },
      async group({ tabIds, groupId }) {
        const id = groupId ?? GROUP;
        for (const tabId of tabIds) tabs.get(tabId).groupId = id;
        return id;
      },
      async remove(ids) {
        if (hooks.removeFails) throw new Error('The tab cannot be closed');
        for (const id of [].concat(ids)) { tabs.delete(id); log.removed.push(id); log.order.push(`tabs.remove ${id}`); }
      },
      onCreated: { addListener(fn) { tabsCreatedListener = fn; } },
      onUpdated: { addListener(fn) { tabsUpdatedListener = fn; } },
      async query({ groupId }) { return [...tabs.values()].filter(tab => tab.groupId === groupId); },
    },
    tabGroups: {
      async get(id) { if (!groupsOpen.has(id)) throw new Error('No group'); return { id }; },
      async update() {},
    },
    permissions: {
      async contains({ origins }) {
        if (hooks.permissionGate) await hooks.permissionGate;
        return origins.every(origin => grantedOrigins.has(origin.replace(/\/\*$/, '')));
      },
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
      async attach({ tabId }) { log.attached.push(tabId); log.order.push(`attach ${tabId}`); },
      async detach({ tabId }) { log.detached.push(tabId); log.order.push(`detach ${tabId}`); },
      onEvent: {
        addListener(fn) { loadListeners.push(fn); },
        removeListener(fn) { loadListeners = loadListeners.filter(item => item !== fn); },
      },
      onDetach: { addListener(fn) { detachListener = fn; } },
      async sendCommand(target, method, params = {}) {
        const { tabId, sessionId } = target;
        log.cdp.push({ tabId, sessionId, method, params });
        log.order.push(`${method}${sessionId ? ` @${sessionId}` : ''}`);
        const sessionFailure = hooks.failSession[sessionId];
        if (sessionFailure && sessionFailure.method === method) throw new Error(sessionFailure.message);
        if (method === 'Page.setDownloadBehavior') {
          if (denyError === null) return {};
          // Shaped like the rejection Chrome returns today: code -32000 with this message.
          const error = new Error(denyError);
          error.code = -32000;
          throw error;
        }
        if (method === 'Page.enable') return {};
        if (method === 'Page.getFrameTree') return { frameTree: { frame: { id: sessionId ? `${sessionId}-main` : 'main', url: tabs.get(tabId)?.url } } };
        if (method === 'Fetch.enable') assertValidFetchFilter(params);
        if (method === 'Network.enable') { networkOn.add(sessionId || tabId); return {}; }
        if (method === 'Network.setBypassServiceWorker' && !networkOn.has(sessionId || tabId)) {
          // Chromium ignores the override on a session whose Network domain is off.
          log.bypassIgnored.push(sessionId || tabId);
        }
        if (method === 'Fetch.enable' || method === 'Fetch.disable' || method === 'Target.setAutoAttach'
          || method === 'Network.setBypassServiceWorker' || method === 'Runtime.runIfWaitingForDebugger'
          || method === 'Target.closeTarget') return {};
        if (method === 'Page.setPrerenderingAllowed') {
          if (hooks.prerenderMissing) throw new Error("'Page.setPrerenderingAllowed' wasn't found");
          return {};
        }
        if (method === 'Fetch.continueRequest') { log.continued.push(params.requestId); verdicts.get(params.requestId)?.('continued'); return {}; }
        if (method === 'Fetch.failRequest') {
          if (hooks.failRequestError) throw new Error(hooks.failRequestError);
          log.failed.push(params.requestId); verdicts.get(params.requestId)?.('failed'); return {};
        }
        if (method === 'Page.navigate') {
          log.navigations.push(params.url);
          if (params.url === 'about:blank') {
            if (hooks.blankNavigateResult) return hooks.blankNavigateResult;
            tabs.get(tabId).url = 'about:blank';
            return {};
          }
          navigateThrough(tabId, [params.url, ...(hooks.redirects[params.url] || [])]);
          return {};
        }
        if (method === 'Page.getNavigationHistory') return hooks.history;
        if (method === 'Page.navigateToHistoryEntry') {
          const entry = hooks.history.entries.find(item => item.id === params.entryId);
          log.navigations.push(entry.url);
          navigateThrough(tabId, [entry.url]);
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
        log.connects += 1;
        return {
          onMessage: { addListener(fn) { nativeListener = fn; } },
          onDisconnect: { addListener() {} },
          postMessage(reply) { pending.get(reply.id)?.(reply); pending.delete(reply.id); },
          disconnect() {},
        };
      },
      onMessage: { addListener(fn) { workerOnMessage = fn; } },
      onStartup: { addListener(fn) { startupListener = fn; } },
      onInstalled: { addListener(fn) { installedListener = fn; } },
    },
  };

  const context = vm.createContext({ chrome, URL, setTimeout, clearTimeout, console });
  vm.runInContext(workerSource, context, { filename: 'service_worker.js' });
  await flush();
  if (expectConnected) assert.equal(typeof nativeListener, 'function', 'native listener installed');
  else assert.equal(nativeListener, undefined, 'a stopped worker opened no native port');
  assert.equal(typeof workerOnMessage, 'function', 'popup message listener installed');

  return {
    chrome, log, hooks, context,
    store: () => store,
    local: () => local,
    setLocal(value) { local = { ...local, ...value }; },
    fireStartup() { startupListener(); },
    fireInstalled() { installedListener({ reason: 'update' }); },
    fireFetch(tabId, params, sessionId) {
      loadListeners.slice().forEach(fn => fn(sessionId ? { tabId, sessionId } : { tabId }, 'Fetch.requestPaused', params));
    },
    fireEvent(source, method, params) { loadListeners.slice().forEach(fn => fn(source, method, params)); },
    fireTabCreated(tab) { return tabsCreatedListener(tab); },
    fireTabUpdated(tabId, changeInfo) { return tabsUpdatedListener(tabId, changeInfo); },
    tabs,
    cdpOf(sessionId) { return log.cdp.filter(call => call.sessionId === sessionId).map(call => call.method); },
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
  assert.equal(browser.local().chimeraActionsDisabled, true, 'the Cancel switch is in storage.local (ext-08)');
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
  assert.equal(browser.local().chimeraActionsDisabled, false, 'storage flag cleared by the owner');
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

// 7. ext-02: an open redirector on a granted site. The request to the granted origin continues; the hop that
//    leaves for an origin nobody granted is failed BEFORE it is sent, the navigation reports the refusal, and
//    the popup is told which origin to offer.
{
  const browser = await openBrowser();
  const leak = 'https://collector.example/?d=SECRET';
  browser.hooks.redirects[`${SHOP}/url?q=${leak}`] = [leak];
  const reply = await browser.ask('navigate', { url: `${SHOP}/url?q=${leak}` });
  assert.equal(reply.ok, false, JSON.stringify(reply));
  assert.match(reply.error, /not been granted/);
  const sent = browser.log.requests.find(item => item.url === leak);
  assert.ok(sent, 'the redirect hop was seen');
  assert.ok(browser.log.failed.includes(sent.requestId), 'the hop to the ungranted origin was failed');
  assert.ok(!browser.log.continued.includes(sent.requestId), 'and never continued');
  assert.equal(browser.store().chimeraRequestedOrigin, 'https://collector.example', 'the popup can offer Allow for it');
}

// 8. A redirect that stays on granted origins is not in the way.
{
  const browser = await openBrowser({ granted: [SHOP, 'https://pay.example'] });
  browser.hooks.redirects[`${SHOP}/go`] = ['https://pay.example/landing'];
  const reply = await browser.ask('navigate', { url: `${SHOP}/go` });
  assert.equal(reply.ok, true, JSON.stringify(reply));
  assert.deepEqual(browser.log.failed, []);
}

// 9. Back: an earlier history entry on an ungranted origin is refused before the request, a granted one works.
{
  const browser = await openBrowser();
  browser.hooks.history = { currentIndex: 1, entries: [{ id: 10, url: 'https://elsewhere.example/page' }, { id: 11, url: `${SHOP}/now` }] };
  const refused = await browser.ask('back');
  assert.equal(refused.ok, false, JSON.stringify(refused));
  assert.match(refused.error, /not been granted/);
  assert.deepEqual(browser.log.navigations, [], 'no history navigation was started');
  assert.equal(browser.store().chimeraRequestedOrigin, 'https://elsewhere.example');

  browser.hooks.history = { currentIndex: 1, entries: [{ id: 10, url: `${SHOP}/before` }, { id: 11, url: `${SHOP}/now` }] };
  const allowed = await browser.ask('back');
  assert.equal(allowed.ok, true, JSON.stringify(allowed));
  assert.deepEqual(browser.log.navigations, [`${SHOP}/before`]);
}

// 10. A navigation the page starts itself (a script, a clicked link) is held to the grant too, and an embedded
//     frame is not mistaken for the page.
{
  const browser = await openBrowser();
  browser.fireFetch(1, { requestId: 'own-1', frameId: 'main', resourceType: 'Document', request: { url: 'https://tracker.example/' } });
  browser.fireFetch(1, { requestId: 'own-2', frameId: 'main', resourceType: 'Document', request: { url: `${SHOP}/next` } });
  browser.fireFetch(1, { requestId: 'own-3', frameId: 'child', resourceType: 'Document', request: { url: 'https://captcha.example/frame' } });
  await flush(); await flush(); await flush();
  assert.deepEqual(browser.log.failed, ['own-1'], 'only the top-level hop to the ungranted origin fails');
  assert.deepEqual(browser.log.continued.sort(), ['own-2', 'own-3']);
  // Anything that is not plain http(s) has no origin to grant, so it is refused.
  assert.equal(browser.context.navigationOrigin('data:text/html,hi'), null);
  assert.equal(browser.context.navigationOrigin('javascript:alert(1)'), null);
  assert.equal(browser.context.navigationOrigin('https://shop.example:8443/x'), 'https://shop.example:8443');
}

// 11. ext-04: Stop holds. It is written to storage.local, the popup shows it, and neither a restart of the
//     worker nor an event that wakes it opens the native port again. Only the owner's Start does.
{
  const first = await openBrowser();
  const popup = openPopup(first);
  await flush();
  assert.equal(popup.get('stop').hidden, false);
  assert.equal(popup.get('start').hidden, true);
  await popup.get('stop').click();
  await flush();
  assert.equal(first.local().chimeraStopped, true, 'Stop is persistent');
  assert.equal(popup.get('start').hidden, false, 'the popup offers Start once stopped');
  assert.equal(popup.get('stop').hidden, true);
  assert.equal(popup.get('stoppedNote').hidden, false);

  // The worker is recycled (session storage survives), then Chrome itself restarts (it does not).
  for (const initialStore of [first.store(), null]) {
    const again = await openBrowser({ initialStore, initialLocal: first.local(), expectConnected: false });
    assert.equal(again.log.connects, 0, 'no native port after the worker restarted');
    again.fireStartup(); again.fireInstalled();
    await flush();
    assert.equal(again.log.connects, 0, 'neither onStartup nor onInstalled reconnects');
    const status = await again.sendToWorker({ op: 'status' });
    assert.equal(status.stopped, true);
    assert.equal(status.status, 'Stopped', 'the popup shows the stored state');
    const repopup = openPopup(again);
    await flush();
    assert.equal(repopup.get('start').hidden, false);
    await repopup.get('start').click();
    await flush();
    assert.equal(again.log.connects, 1, 'Start is what reconnects');
    assert.equal(again.local().chimeraStopped, false);
    assert.equal(repopup.get('stop').hidden, false);
  }
}

// 11b. A request that reaches a live port while Stop holds is refused (defence in depth).
{
  const browser = await openBrowser();
  browser.setLocal({ chimeraStopped: true }); // Stop recorded while a port is still open
  const reply = await browser.ask('read');
  assert.equal(reply.ok, false);
  assert.match(reply.error, /click Start/);
  assert.equal(browser.context.mayConnect(true), false);
  assert.equal(browser.context.mayConnect(false), true);
}

// 12. ext-08: the debugging bar's Cancel is in storage.local too; it survives a restart and an app request
//     still cannot clear it.
{
  const first = await openBrowser();
  first.fireDetach({ tabId: 1 }, 'canceled_by_user');
  await flush(); await flush();
  const again = await openBrowser({ initialLocal: first.local() });
  const blocked = await again.ask('navigate', { url: `${SHOP}/shop` });
  assert.equal(blocked.ok, false);
  assert.match(blocked.error, /Resume Chimera actions/);
}

// 13. First navigation (Codex P1-1): with no tab yet, the tab is created BLANK, attached, filtered and cleared
//     of service workers, and only then navigated. Creating it on the URL would have let the first request
//     (and any redirect it follows) leave before the filter existed.
{
  const browser = await openBrowser({ noTabs: true });
  const reply = await browser.ask('navigate', { url: `${SHOP}/start` });
  assert.equal(reply.ok, true, JSON.stringify(reply));
  assert.deepEqual(browser.log.unprotectedLoads, [], 'no tab was created on a real URL');
  const at = prefix => browser.log.order.findIndex(entry => entry.startsWith(prefix));
  assert.ok(at('tabs.create about:blank') >= 0);
  assert.ok(at('tabs.create') < at('attach'), 'created, then attached');
  assert.ok(at('attach') < at('Fetch.enable'), 'then filtered');
  assert.ok(at('Fetch.enable') < at('Network.setBypassServiceWorker') || at('Network.setBypassServiceWorker') >= 0);
  assert.ok(at('Fetch.enable') < at('Page.navigate'), 'the URL loads only after the filter is on');
  assert.ok(at('Network.setBypassServiceWorker') < at('Page.navigate'), 'and after service workers are bypassed');
  assert.ok(at('Target.setAutoAttach') < at('Page.navigate'), 'and after child targets are held');
}

// 14. What the protection turns on for a tab (Codex P1-3): Document AND Prefetch are held at the Request
//     stage, service workers are bypassed, prerender is off, children attach paused.
{
  const browser = await openBrowser();
  await browser.ask('read');
  const mine = browser.log.cdp.filter(call => call.tabId === 1 && !call.sessionId);
  const fetchEnable = mine.find(call => call.method === 'Fetch.enable');
  assert.deepEqual(plain(fetchEnable.params.patterns.map(item => item.resourceType).sort()), ['Document', 'Fetch']);
  assert.ok(fetchEnable.params.patterns.every(item => item.requestStage === 'Request'));
  assert.deepEqual(plain(mine.find(call => call.method === 'Network.setBypassServiceWorker').params), { bypass: true });
  assert.deepEqual(plain(mine.find(call => call.method === 'Page.setPrerenderingAllowed').params), { isAllowed: false });
  assert.deepEqual(plain(mine.find(call => call.method === 'Target.setAutoAttach').params),
    { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
  // Older Chrome without Page.setPrerenderingAllowed still works: the missing command is tolerated.
  const older = await openBrowser();
  older.hooks.prerenderMissing = true;
  const reply = await older.ask('read');
  assert.equal(reply.ok, true, JSON.stringify(reply));
}

// 15. Prefetch to an origin nobody granted is failed, never continued, and does not make the popup ask for it.
//     Chromium's filter has no 'Prefetch' type (the interceptor files prefetch under 'Fetch'), so a prefetch is
//     recognised by its type in the event or by its purpose header; an ordinary fetch/XHR is left alone.
{
  const browser = await openBrowser();
  const evil = 'https://collector.example/?d=SECRET';
  browser.fireFetch(1, { requestId: 'pf-1', frameId: 'main', resourceType: 'Prefetch', request: { url: evil } });
  browser.fireFetch(1, { requestId: 'pf-2', frameId: 'main', resourceType: 'Prefetch', request: { url: `${SHOP}/next` } });
  browser.fireFetch(1, { requestId: 'pf-3', frameId: 'main', resourceType: 'Fetch', request: { url: evil, headers: { 'Sec-Purpose': 'prefetch' } } });
  browser.fireFetch(1, { requestId: 'pf-4', frameId: 'main', resourceType: 'Fetch', request: { url: evil, headers: { Purpose: 'prefetch' } } });
  browser.fireFetch(1, { requestId: 'xhr-1', frameId: 'main', resourceType: 'XHR', request: { url: evil } });
  browser.fireFetch(1, { requestId: 'fetch-1', frameId: 'main', resourceType: 'Fetch', request: { url: evil, headers: { Accept: '*/*' } } });
  await flush(); await flush(); await flush(); await flush();
  assert.deepEqual(browser.log.failed.sort(), ['pf-1', 'pf-3', 'pf-4']);
  assert.deepEqual(browser.log.continued.sort(), ['fetch-1', 'pf-2', 'xhr-1'], 'ordinary fetch/XHR pass straight through');
  assert.equal(browser.store().chimeraRequestedOrigin, undefined, 'a page-driven prefetch never prompts the owner');
}

// 15b. The filter the worker sends must be one Chromium accepts: the mock rejects any other resourceType the
//      way fetch_handler.cc does, so a protocol-only name ('Prefetch') would fail every attach here.
{
  assert.throws(() => assertValidFetchFilter({ patterns: [{ resourceType: 'Prefetch' }] }), /Unknown resource type in fetch filter/);
  assert.doesNotThrow(() => assertValidFetchFilter({ patterns: [{ resourceType: 'Document' }, { resourceType: 'Fetch' }] }));
  const browser = await openBrowser();
  const reply = await browser.ask('read');
  assert.equal(reply.ok, true, `attach survives the strict filter check: ${JSON.stringify(reply)}`);
}

// 15c. Network is enabled before the service-worker bypass, on the tab and on every protected child, so the
//      override is not ignored.
{
  const browser = await openBrowser();
  await browser.ask('read');
  browser.fireEvent({ tabId: 1 }, 'Target.attachedToTarget', { sessionId: 'pg', waitingForDebugger: true, targetInfo: { type: 'page', targetId: 'T-pg' } });
  await flush(); await flush();
  for (const session of [undefined, 'pg']) {
    const calls = session ? browser.cdpOf(session) : browser.log.cdp.filter(call => call.tabId === 1 && !call.sessionId).map(call => call.method);
    assert.ok(calls.indexOf('Network.enable') >= 0 && calls.indexOf('Network.enable') < calls.indexOf('Network.setBypassServiceWorker'),
      `Network.enable precedes the bypass (${session || 'tab'})`);
  }
  assert.deepEqual(browser.log.bypassIgnored, [], 'no bypass was sent to a session with Network off');
}

// 15d. Stop's fallback: a tab that cannot be closed and whose about:blank navigation is refused (errorText, not
//      an exception) is a failure: reported, and the tab stays attached and held instead of being let go.
{
  const browser = await openBrowser();
  browser.hooks.removeFails = true;
  browser.hooks.blankNavigateResult = { errorText: 'net::ERR_ABORTED' };
  const result = await browser.sendToWorker({ op: 'stop' });
  assert.equal(result.ok, true);
  assert.ok(result.issues.some(text => /could not be closed/.test(text)), `reported: ${JSON.stringify(result.issues)}`);
  assert.deepEqual(browser.log.detached, [], 'the page is still open, so the debugger is not released');
  // And when the blank navigation does go through, the tab is released.
  const fine = await openBrowser();
  fine.hooks.removeFails = true;
  const done = await fine.sendToWorker({ op: 'stop' });
  assert.deepEqual(plain(done.issues), []);
  assert.deepEqual(fine.log.detached, [1]);
}

// 16. Child targets the PAGE session delivers (prerender, out-of-process iframes, workers): each arrives paused,
//     is filtered, and only then resumed; a request from it to an ungranted origin is failed on ITS session; one
//     that cannot be protected is closed, not resumed. This does NOT cover window.open popups: Chromium pauses
//     those only for browser-level sessions, which chrome.debugger does not have (see REVISAO.md).
{
  const browser = await openBrowser();
  await browser.ask('read');
  browser.fireEvent({ tabId: 1 }, 'Target.attachedToTarget', {
    sessionId: 'pop', waitingForDebugger: true, targetInfo: { type: 'page', targetId: 'T-pop' },
  });
  await flush(); await flush();
  const popup = browser.cdpOf('pop');
  assert.ok(popup.includes('Fetch.enable') && popup.includes('Network.setBypassServiceWorker') && popup.includes('Target.setAutoAttach'));
  assert.equal(popup[popup.length - 1], 'Runtime.runIfWaitingForDebugger', 'resumed LAST, after it is protected');
  browser.fireFetch(1, { requestId: 'pop-1', frameId: 'pop-main', resourceType: 'Document', request: { url: 'https://collector.example/?d=SECRET' } }, 'pop');
  browser.fireFetch(1, { requestId: 'pop-2', frameId: 'pop-main', resourceType: 'Document', request: { url: `${SHOP}/ok` } }, 'pop');
  await flush(); await flush(); await flush();
  assert.deepEqual(browser.log.failed, ['pop-1']);
  assert.deepEqual(browser.log.continued, ['pop-2']);
  assert.ok(browser.log.cdp.some(call => call.sessionId === 'pop' && call.method === 'Fetch.failRequest'), 'failed on the popup session');

  // A worker and an out-of-process iframe are only resumed (iframes also watch their own children).
  browser.fireEvent({ tabId: 1 }, 'Target.attachedToTarget', { sessionId: 'wk', waitingForDebugger: true, targetInfo: { type: 'service_worker', targetId: 'T-wk' } });
  await flush(); await flush();
  assert.deepEqual(browser.cdpOf('wk'), ['Runtime.runIfWaitingForDebugger']);

  // A page that cannot be protected is closed through its parent and never resumed.
  browser.hooks.failSession.bad = { method: 'Fetch.enable', message: 'no way' };
  browser.fireEvent({ tabId: 1 }, 'Target.attachedToTarget', { sessionId: 'bad', waitingForDebugger: true, targetInfo: { type: 'page', targetId: 'T-bad' } });
  await flush(); await flush();
  assert.ok(browser.log.cdp.some(call => !call.sessionId && call.method === 'Target.closeTarget' && call.params.targetId === 'T-bad'));
  assert.ok(!browser.cdpOf('bad').includes('Runtime.runIfWaitingForDebugger'), 'never resumed');
  const status = await browser.sendToWorker({ op: 'status' });
  assert.ok(status.issues.some(text => /could not be protected/.test(text)), 'reported to the popup');
}

// 16b. A tab opened by a Chimera tab is closed at once; one with another opener is left alone.
{
  const browser = await openBrowser();
  browser.tabs.set(30, { id: 30, url: 'about:blank' });
  browser.tabs.set(31, { id: 31, url: 'about:blank' });
  await browser.fireTabCreated({ id: 30, openerTabId: 1 });
  await browser.fireTabCreated({ id: 31, openerTabId: 99 });
  await browser.fireTabCreated({ id: 32 });
  assert.deepEqual(browser.log.removed, [30]);
}

// 17. Stop does not cancel an operation already admitted (Codex P1-4): the permission check is held, Stop runs
//     to completion, the check is released, and nothing is created or navigated afterwards.
{
  const browser = await openBrowser({ noTabs: true });
  let release;
  browser.hooks.permissionGate = new Promise(resolve => { release = resolve; });
  browser.ask('navigate', { url: `${SHOP}/late` }); // admitted; its reply never comes, the port is gone after Stop
  await flush();
  const stopped = await browser.sendToWorker({ op: 'stop' });
  assert.equal(stopped.ok, true);
  release();
  browser.hooks.permissionGate = null;
  await flush(); await flush(); await flush();
  assert.deepEqual(browser.log.created, [], 'no tab was opened after Stop');
  assert.deepEqual(browser.log.navigations, [], 'nothing was navigated after Stop');
  assert.deepEqual(browser.log.attached, [], 'nothing was attached after Stop');
}

// 17b. The same for Start-after-Stop: an operation admitted BEFORE a Stop is not revived by a later Start.
{
  const browser = await openBrowser({ noTabs: true });
  let release;
  browser.hooks.permissionGate = new Promise(resolve => { release = resolve; });
  browser.ask('navigate', { url: `${SHOP}/late` });
  await flush();
  await browser.sendToWorker({ op: 'stop' });
  await browser.sendToWorker({ op: 'start' });
  release();
  browser.hooks.permissionGate = null;
  await flush(); await flush(); await flush();
  assert.deepEqual(browser.log.created, []);
  assert.deepEqual(browser.log.navigations, []);
}

// 18. Stop order (Codex P1-5): a request held at the filter is FAILED first, then the tab is closed, and only
//     then is the debugger detached. Letting go first would let Chrome release the held request.
{
  const browser = await openBrowser();
  let release;
  browser.hooks.permissionGate = new Promise(resolve => { release = resolve; });
  browser.fireFetch(1, { requestId: 'held-1', frameId: 'main', resourceType: 'Document', request: { url: 'https://collector.example/?d=SECRET' } });
  await flush(); await flush();
  const result = await browser.sendToWorker({ op: 'stop' });
  assert.equal(result.ok, true);
  assert.deepEqual(plain(result.issues), []);
  release();
  browser.hooks.permissionGate = null;
  await flush(); await flush();
  const at = prefix => browser.log.order.findIndex(entry => entry.startsWith(prefix));
  assert.ok(at('Fetch.failRequest') >= 0, 'the held request was failed');
  assert.ok(at('Fetch.failRequest') < at('tabs.remove'), 'failed before the tab is closed');
  assert.ok(at('tabs.remove') < at('detach'), 'closed before the debugger is detached');
  assert.deepEqual(browser.log.failed, ['held-1'], 'failed once, by Stop; the decision that was in flight did not fail it again');
  assert.deepEqual(browser.log.continued, [], 'and never continued');
}

// 18b. If failing the held request is refused, the failure is REPORTED, the tab is still closed before the
//      debugger lets go, and a refused failRequest inside the filter closes the tab as well.
{
  const browser = await openBrowser();
  let release;
  browser.hooks.permissionGate = new Promise(resolve => { release = resolve; });
  browser.fireFetch(1, { requestId: 'held-2', frameId: 'main', resourceType: 'Document', request: { url: 'https://collector.example/' } });
  await flush(); await flush();
  browser.hooks.failRequestError = 'Invalid InterceptionId';
  const result = await browser.sendToWorker({ op: 'stop' });
  assert.equal(result.ok, true);
  assert.ok(result.issues.some(text => /could not be failed/.test(text)), `reported: ${JSON.stringify(result.issues)}`);
  const at = prefix => browser.log.order.findIndex(entry => entry.startsWith(prefix));
  assert.ok(at('tabs.remove') >= 0 && at('tabs.remove') < at('detach'), 'the tab is closed before detaching');
  release();
  browser.hooks.permissionGate = null;
  await flush();

  const other = await openBrowser();
  other.hooks.failRequestError = 'Invalid InterceptionId';
  other.fireFetch(1, { requestId: 'bad-1', frameId: 'main', resourceType: 'Document', request: { url: 'https://collector.example/' } });
  await flush(); await flush(); await flush();
  assert.ok(other.log.removed.includes(1), 'a request that cannot be failed closes its tab');
  const status = await other.sendToWorker({ op: 'status' });
  assert.ok(status.issues.some(text => /could not be blocked/.test(text)), 'and is reported');
}

// 19. P2: a tab the owner drags out of the Chimera group is no longer filtered and is detached.
{
  const browser = await openBrowser();
  await browser.ask('read');
  await browser.fireTabUpdated(1, { groupId: GROUP }); // same group: untouched
  await flush(); await flush();
  assert.deepEqual(browser.log.detached, []);
  await browser.fireTabUpdated(1, { groupId: -1 });
  await flush(); await flush();
  assert.ok(browser.log.cdp.some(call => call.tabId === 1 && call.method === 'Fetch.disable'), 'the filter is turned off');
  assert.deepEqual(browser.log.detached, [1], 'and the tab is let go');
  assert.deepEqual(plain(browser.store().chimeraTabIds), [], 'it is no longer a Chimera tab');
  assert.equal(browser.store().chimeraActiveTabId, undefined);
}

console.log('service worker recovery cases: 27');

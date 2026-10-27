// Tab selection in the extension's service worker: every debugger attach, navigation and read must
// land on a web tab inside the Chimera group. Run through `tests/test_browser_chrome_e2e_tabs.py`.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
// CHIMERA_SW_PATH lets the pytest wrapper point the same scenarios at another copy (e.g. the pre-fix file).
const source = fs.readFileSync(process.env.CHIMERA_SW_PATH || path.join(root, 'extensions/chrome/service_worker.js'), 'utf8');

const SEARCH = 'https://www.google.com/search?q=chimera&hl=pt-BR';
const CHIMERA_GROUP = 7;

// `tabs` maps id -> {url, groupId}. `groups` lists the group ids that still exist.
async function run(name, { tabs, groups = [CHIMERA_GROUP], op, url }) {
  const tabMap = new Map(Object.entries(tabs).map(([id, tab]) => [Number(id), { id: Number(id), ...tab }]));
  const groupSet = new Set(groups);
  let nextTabId = 100;
  let nextGroupId = 50;
  let store = {
    chimeraGroupId: CHIMERA_GROUP, chimeraActiveTabId: 1, chimeraTabIds: [...tabMap.keys()],
  };
  const calls = [];
  const loadListeners = [];
  let nativeListener;
  let port;
  const chrome = {
    storage: { session: {
      async get() { return store; },
      async set(value) { store = { ...store, ...value }; },
    } },
    tabs: {
      async get(id) {
        const tab = tabMap.get(id);
        if (!tab) throw new Error(`No tab ${id}`);
        return tab;
      },
      async create({ url: createUrl, active }) {
        const id = nextTabId++;
        const tab = { id, url: createUrl || 'chrome://newtab/', groupId: undefined, active };
        tabMap.set(id, tab);
        calls.push({ method: 'tabs.create', url: createUrl });
        return tab;
      },
      async group({ tabIds, groupId }) {
        const id = groupId ?? nextGroupId++;
        groupSet.add(id);
        for (const tabId of tabIds) tabMap.get(tabId).groupId = id;
        calls.push({ method: 'tabs.group', tabIds, groupId: id });
        return id;
      },
      async remove() {},
      async query({ groupId }) { return [...tabMap.values()].filter(tab => tab.groupId === groupId); },
    },
    tabGroups: {
      async get(id) { if (!groupSet.has(id)) throw new Error('No group'); return { id }; },
      async update() {},
    },
    permissions: { async contains() { return true; } },
    debugger: {
      async attach({ tabId }) {
        // Chrome refuses the debugger on its own pages, with this message (seen live on 26/10).
        if (/^(chrome|about|edge):/.test(tabMap.get(tabId)?.url || '')) throw new Error('Cannot access a chrome:// URL');
        calls.push({ method: 'attach', tabId });
      },
      async detach() {},
      onEvent: {
        addListener(fn) { loadListeners.push(fn); },
        removeListener(fn) { loadListeners.splice(loadListeners.indexOf(fn), 1); },
      },
      onDetach: { addListener() {} },
      async sendCommand({ tabId }, method) {
        calls.push({ method: 'cdp', tabId, cdp: method });
        if (method === 'Accessibility.getFullAXTree') return { nodes: [] };
        if (method === 'Page.navigate') {
          setImmediate(() => loadListeners.slice().forEach(fn => fn({ tabId }, 'Page.loadEventFired')));
          return {};
        }
        if (method === 'Page.enable' || method === 'Page.setDownloadBehavior') return {};
        throw new Error(`Unexpected CDP method ${method}`);
      },
    },
    runtime: {
      connectNative() {
        port = { onMessage: { addListener(fn) { nativeListener = fn; } }, onDisconnect: { addListener() {} }, postMessage() {}, disconnect() {} };
        return port;
      },
      onMessage: { addListener() {} },
      onStartup: { addListener() {} },
      onInstalled: { addListener() {} },
    },
  };
  vm.runInContext(source, vm.createContext({ chrome, URL, setTimeout, clearTimeout, console }), { filename: 'service_worker.js' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof nativeListener, 'function', `${name}: native listener installed`);
  const reply = await new Promise(resolve => {
    port.postMessage = resolve;
    nativeListener({ id: 1, op, url });
  });
  const attached = calls.filter(call => call.method === 'attach').map(call => call.tabId);
  return { reply, calls, attached, name };
}

// 1. The group's own tab is a Chrome page (the owner navigated it, or it is still the new-tab page).
//    The search must open a fresh tab in the group on the results URL and attach only to that tab.
{
  const r = await run('group tab on chrome://, navigate', {
    tabs: { 1: { url: 'chrome://newtab/', groupId: CHIMERA_GROUP }, 9: { url: 'chrome://extensions/', groupId: undefined } },
    op: 'navigate', url: SEARCH,
  });
  assert.equal(r.reply.ok, true, `navigate recovers: ${JSON.stringify(r.reply)}`);
  assert.deepEqual(r.attached, [100], 'attach goes only to the new group tab');
  assert.ok(r.calls.some(call => call.method === 'tabs.create' && call.url === SEARCH), 'the new tab opens on the results URL');
  assert.ok(r.calls.some(call => call.method === 'tabs.group' && call.groupId === CHIMERA_GROUP), 'the new tab joins the Chimera group');
  assert.ok(!r.calls.some(call => call.tabId === 1 || call.tabId === 9), 'neither the chrome:// tab nor the focused tab is touched');
}

// 2. A read with no web tab cannot attach to the chrome:// page: it fails with a clear reason, before attach.
{
  const r = await run('read on chrome page', {
    tabs: { 1: { url: 'chrome://extensions/', groupId: CHIMERA_GROUP } }, op: 'read',
  });
  assert.equal(r.reply.ok, false);
  assert.match(r.reply.error, /navigate to a site first/);
  assert.deepEqual(r.attached, [], 'no debugger attach to a chrome:// page');
  assert.equal(r.calls.some(call => call.method === 'cdp'), false);
}

// 3. A web group tab is reused: no new tab, attach to that tab.
{
  const r = await run('navigate reuses web group tab', {
    tabs: { 1: { url: 'https://shop.example/', groupId: CHIMERA_GROUP }, 9: { url: 'chrome://extensions/', groupId: undefined } },
    op: 'navigate', url: SEARCH,
  });
  assert.equal(r.reply.ok, true, JSON.stringify(r.reply));
  assert.deepEqual(r.attached, [1]);
  assert.equal(r.calls.some(call => call.method === 'tabs.create'), false, 'no tab is created when the group has a web tab');
}

// 4. The owner's focused tab, outside the group and on a web page, is never attached to by a read.
{
  const r = await run('read with focused tab outside group', {
    tabs: { 1: { url: 'https://shop.example/', groupId: CHIMERA_GROUP }, 9: { url: 'https://other.example/', groupId: undefined } },
    op: 'read',
  });
  assert.equal(r.reply.ok, true, JSON.stringify(r.reply));
  assert.deepEqual(r.attached, [1]);
}

// 5. The owner closed the Chimera group: a navigate starts a new group on the results URL, and a read
//    with no group refuses to create a blank tab to read.
{
  const r = await run('navigate after the group was closed', {
    tabs: { 1: { url: 'https://shop.example/', groupId: CHIMERA_GROUP } }, groups: [],
    op: 'navigate', url: SEARCH,
  });
  assert.equal(r.reply.ok, true, JSON.stringify(r.reply));
  assert.deepEqual(r.attached, [100]);
  assert.ok(r.calls.some(call => call.method === 'tabs.create' && call.url === SEARCH));
}
{
  const r = await run('read after the group was closed', {
    tabs: { 1: { url: 'https://shop.example/', groupId: CHIMERA_GROUP } }, groups: [], op: 'read',
  });
  assert.equal(r.reply.ok, false);
  assert.match(r.reply.error, /navigate to a site first/);
  assert.equal(r.calls.some(call => call.method === 'tabs.create'), false, 'no blank tab is created to read');
  assert.deepEqual(r.attached, []);
}

console.log('chimera tab selection cases: 6');

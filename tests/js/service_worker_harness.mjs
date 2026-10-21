import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const source = fs.readFileSync(path.join(root, 'extensions/chrome/service_worker.js'), 'utf8');

async function scenario(name, options = {}) {
  let messageHandler;
  let nativeHandler;
  let storage = {
    chimeraGroupId: 7,
    chimeraTabIds: [1],
    chimeraActiveTabId: 1,
    chimeraRequestedOrigin: undefined,
  };
  const calls = [];
  let describes = 0;
  const nativePort = {
    onMessage: { addListener(callback) { nativeHandler = callback; } },
    onDisconnect: { addListener() {} },
    postMessage(reply) { this.reply = reply; },
    disconnect() {},
  };
  const targetAttrs = options.href
    ? ['href', options.href, 'aria-label', 'Home', ...(options.download ? ['download', ''] : []),
      ...(options.target ? ['target', options.target] : []), ...(options.ping ? ['ping', options.ping] : [])]
    : ['type', 'search', 'aria-label', 'Search'];
  const target = { nodeName: options.href ? 'A' : 'INPUT', backendNodeId: 12, frameId: 'frame', attributes: targetAttrs };
  const rootNode = {
    nodeName: '#document', backendNodeId: 1, documentURL: 'https://shop.example/',
    children: [target],
  };
  const chrome = {
    storage: { session: {
      async get() { return storage; },
      async set(value) { storage = { ...storage, ...value }; },
    } },
    tabs: {
      async get() { return { id: 1, groupId: 7, url: 'https://shop.example/' }; },
      async create() { return { id: 1 }; }, async group() { return 7; },
      async remove() {},
    },
    tabGroups: { async get() { return { id: 7 }; }, async update() {} },
    permissions: { async contains() { return true; } },
    debugger: {
      async attach() {}, async detach() {},
      onEvent: { addListener() {}, removeListener() {} },
      onDetach: { addListener() {} },
      async sendCommand(_target, method, params = {}) {
        calls.push({ method, params });
        if (method === 'DOM.describeNode') { describes += 1; return { node: target }; }
        if (method === 'DOM.getDocument') return { root: rootNode };
        if (method === 'Page.getFrameTree') return { frameTree: { frame: { id: 'frame', url: 'https://shop.example/' } } };
        if (method === 'Accessibility.getPartialAXTree') return { nodes: [{ backendDOMNodeId: 12, name: { value: options.href ? 'Home' : 'Search' } }] };
        if (method === 'Accessibility.getFullAXTree') return { nodes: [] };
        if (method === 'DOM.resolveNode') return { object: { objectId: `node-${params.backendNodeId}` } };
        if (method === 'Runtime.callFunctionOn') {
          const declaration = params.functionDeclaration || '';
          if (declaration.includes('const owned=this.form')) return { result: { value: null } };
          if (declaration.includes('const labels=this.labels')) return { result: { value: { labels: [], named: '' } } };
          if (declaration.includes('return this.href')) {
            if (options.changeHref && describes > 1) return { result: { value: 'https://shop.example/changed' } };
            return { result: { value: options.href || '' } };
          }
          if (declaration.includes('function(text)')) {
            const execCalls = [];
            const element = {
              tagName: 'INPUT', type: 'search', autocomplete: '', name: '', id: '', placeholder: '', labels: [],
              ownerDocument: { execCommand(...args) { execCalls.push(args); return true; } },
              getAttribute() { return ''; }, getRootNode() { return { activeElement: options.focusChanges ? null : element }; },
              focus() {},
            };
            const action = new Function(`return (${declaration})`)();
            const value = action.call(element, params.arguments[0].value);
            options.execCalls = execCalls;
            return { result: { value } };
          }
          return { result: { value: params.objectId === 'node-99' } };
        }
        if (method === 'Runtime.evaluate') return { result: { objectId: params.expression } };
        if (method === 'DOMDebugger.getEventListeners') {
          return { listeners: options.windowListener && params.objectId === 'window'
            ? [{ type: 'click' }] : [] };
        }
        if (method === 'DOM.getBoxModel') return { model: { content: [0, 0, 100, 0, 100, 40, 0, 40] } };
        if (method === 'DOM.getNodeForLocation') return { backendNodeId: options.overlap ? 99 : 12 };
        if (method === 'Runtime.releaseObject') return {};
        if (method === 'DOM.focus' || method.startsWith('Input.')) return {};
        throw new Error(`Unexpected CDP method ${method}`);
      },
    },
    runtime: {
      connectNative() { return nativePort; },
      onMessage: { addListener(callback) { messageHandler = callback; } },
      onStartup: { addListener() {} }, onInstalled: { addListener() {} },
    },
  };
  const context = vm.createContext({ chrome, URL, setTimeout, clearTimeout, console });
  vm.runInContext(source, context, { filename: 'service_worker.js' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(typeof nativeHandler, 'function', 'native message listener installed');
  const reply = await new Promise(resolve => {
    nativePort.postMessage = value => resolve(value);
    nativeHandler({ id: 1, op: 'click', ref_node: 12, expected: options.expected, allowlisted: true });
  });
  return { reply, calls, context, messageHandler, name };
}

// Atomic typing: failed focus validation must prevent both insertion APIs.
async function typeFocusChanges() {
  // The extension's native request path uses the same handler; use a search target.
  let nativeHandler;
  const calls = [];
  let store = { chimeraGroupId: 7, chimeraTabIds: [1], chimeraActiveTabId: 1 };
  const port = { onMessage: { addListener(fn) { nativeHandler = fn; } }, onDisconnect: { addListener() {} }, postMessage() {}, disconnect() {} };
  const node = { nodeName: 'INPUT', backendNodeId: 12, frameId: 'frame', attributes: ['type', 'search', 'aria-label', 'Search'] };
  const chrome = {
    storage: { session: { async get() { return store; }, async set(v) { store = { ...store, ...v }; } } },
    tabs: { async get() { return { groupId: 7, url: 'https://shop.example/' }; }, async remove() {} },
    tabGroups: { async get() { return {}; } }, permissions: { async contains() { return true; } },
    debugger: { async attach() {}, async detach() {}, onEvent: { addListener() {}, removeListener() {} }, onDetach: { addListener() {} },
      async sendCommand(_t, method, params = {}) {
        calls.push({ method, params });
        if (method === 'DOM.describeNode') return { node };
        if (method === 'DOM.getDocument') return { root: { nodeName: '#document', backendNodeId: 1, documentURL: 'https://shop.example/', children: [node] } };
        if (method === 'Page.getFrameTree') return { frameTree: { frame: { id: 'frame', url: 'https://shop.example/' } } };
        if (method === 'Accessibility.getPartialAXTree') return { nodes: [{ backendDOMNodeId: 12, name: { value: 'Search' } }] };
        if (method === 'DOM.resolveNode') return { object: { objectId: `node-${params.backendNodeId}` } };
        if (method === 'Runtime.callFunctionOn') {
          const fn = params.functionDeclaration || '';
          if (fn.includes('const owned=this.form')) return { result: { value: null } };
          if (fn.includes('const labels=this.labels')) return { result: { value: { labels: [], named: '' } } };
          if (fn.includes('function(text)')) {
          const element = {
            tagName: 'INPUT', type: 'search', autocomplete: '', name: '', id: '', placeholder: '', labels: [],
            ownerDocument: { execCommand() { throw new Error('execCommand must not run after failed focus'); } },
            getAttribute() { return ''; }, getRootNode() { return { activeElement: null }; }, focus() {},
          };
          const action = new Function(`return (${fn})`)();
          return { result: { value: action.call(element, params.arguments[0].value) } };
        }
          return { result: { value: false } };
        }
        if (method === 'Runtime.evaluate') return { result: { objectId: params.expression } };
        if (method === 'DOMDebugger.getEventListeners') return { listeners: [] };
        if (method === 'Runtime.releaseObject') return {};
        if (method === 'Accessibility.getFullAXTree') return { nodes: [] };
        throw new Error(`Unexpected CDP method ${method}`);
      } },
    runtime: { connectNative() { return port; }, onMessage: { addListener() {} }, onStartup: { addListener() {} }, onInstalled: { addListener() {} } },
  };
  vm.runInContext(source, vm.createContext({ chrome, URL, setTimeout, clearTimeout, console }), { filename: 'service_worker.js' });
  await new Promise(resolve => setImmediate(resolve));
  const reply = await new Promise(resolve => { port.postMessage = resolve; nativeHandler({ id: 2, op: 'type', ref_node: 12, text: 'hello' }); });
  assert.equal(reply.ok, false);
  assert.equal(calls.some(call => call.method === 'Input.insertText'), false);
  assert.equal(calls.some(call => call.method === 'Runtime.callFunctionOn' && /execCommand/.test(call.params.functionDeclaration || '')), true);
}

await typeFocusChanges();
const overlap = await scenario('overlap', { href: 'https://shop.example/catalog', overlap: true });
assert.equal(overlap.reply.ok, false, 'overlapping hit is refused');
assert.equal(overlap.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const changed = await scenario('href changed', { href: 'https://shop.example/catalog', changeHref: true,
  expected: JSON.stringify(['a', '', '', 'Home', 'https://shop.example/catalog', '', false, false, '', false, '', 'https://shop.example/']) });
assert.equal(changed.reply.ok, false, 'changed href is refused');
assert.equal(changed.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const windowListener = await scenario('window listener', { href: 'https://shop.example/catalog', windowListener: true,
  expected: JSON.stringify(['a', '', '', 'Home', 'https://shop.example/catalog', '', false, false, '', true, '', 'https://shop.example/']) });
assert.equal(windowListener.reply.ok, false, 'listener on window prevents unapproved click');
assert.equal(windowListener.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
console.log('service worker harness: 4 adversarial cases passed');

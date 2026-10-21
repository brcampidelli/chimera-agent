import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

// The summaries come from the Python engine (`_target_summary`), so both sides are tested against one
// format; run through `tests/test_browser_chrome_e2.py`, which sets the variable.
if (!process.env.CHIMERA_EXPECTED_SUMMARIES) {
  throw new Error('CHIMERA_EXPECTED_SUMMARIES is not set: run this harness through pytest');
}
const expectedSummaries = JSON.parse(process.env.CHIMERA_EXPECTED_SUMMARIES);

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
  let hitChecks = 0;
  let hovered = false;
  const nativePort = {
    onMessage: { addListener(callback) { nativeHandler = callback; } },
    onDisconnect: { addListener() {} },
    postMessage(reply) { this.reply = reply; },
    disconnect() {},
  };
  const tag = (options.tag || (options.href ? 'a' : 'input')).toUpperCase();
  const targetAttrs = [
    ...(options.href ? ['href', options.href] : []),
    ...(options.type ? ['type', options.type] : (tag === 'INPUT' ? ['type', 'search'] : [])),
    'aria-label', options.ariaLabel || (options.href ? 'Home' : tag === 'BUTTON' ? 'Continue' : 'Search'),
    ...(options.autocomplete ? ['autocomplete', options.autocomplete] : []),
    ...(options.download ? ['download', ''] : []),
    ...(options.target ? ['target', options.target] : []),
    ...(options.ping ? ['ping', options.ping] : []),
    ...(options.formmethod ? ['formmethod', options.formmethod] : []),
    ...(options.formaction ? ['formaction', options.formaction] : []),
    ...(options.formtarget ? ['formtarget', options.formtarget] : []),
    ...(options.form ? ['form', options.form] : []),
  ];
  const target = { nodeName: tag, backendNodeId: 12, frameId: 'frame', attributes: targetAttrs };
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
        if (method === 'DOM.describeNode') { describes += 1; return { node: { ...target, attributes: [...targetAttrs] } }; }
        if (method === 'DOM.getAttributes') return { attributes: [...targetAttrs] };
        if (method === 'DOM.getDocument') return { root: rootNode };
        if (method === 'Page.getFrameTree') return { frameTree: { frame: { id: 'frame', url: 'https://shop.example/' } } };
        if (method === 'Accessibility.getPartialAXTree') return { nodes: [{ backendDOMNodeId: 12, name: { value: options.href ? 'Home' : tag === 'BUTTON' ? 'Continue' : 'Search' } }] };
        if (method === 'Accessibility.getFullAXTree') return { nodes: [] };
        if (method === 'DOM.resolveNode') return { object: { objectId: `node-${params.backendNodeId}` } };
        if (method === 'Runtime.callFunctionOn') {
          const declaration = params.functionDeclaration || '';
          if (declaration.includes('return String(this.value || "").length')) return { result: { value: 0 } };
          if (declaration.includes('const owned=this.form')) {
            if (options.form) return { result: { value: options.form } };
            return { result: { value: null } };
          }
          if (declaration.includes('const labels=this.labels')) return { result: { value: { labels: [], named: '' } } };
          if (declaration.includes('return this.href')) {
            if (options.changeHref && describes > 1) return { result: { value: 'https://shop.example/changed' } };
            return { result: { value: options.href || '' } };
          }
          if (declaration.includes('function(text)')) {
            const execCalls = [];
            const dispatched = [];
            class InputElement { }
            class TextareaElement { }
            Object.defineProperty(InputElement.prototype, 'value', {
              get() { return this._value || ''; }, set(value) { this._value = value; }, configurable: true,
            });
            Object.defineProperty(TextareaElement.prototype, 'value', {
              get() { return this._value || ''; }, set(value) { this._value = value; }, configurable: true,
            });
            class InputEvent { constructor(type, init) { this.type = type; this.bubbles = init.bubbles; } }
            const tagName = (options.tag || (options.href ? 'a' : 'input')).toUpperCase();
            const element = {
              tagName, type: options.type || (options.href ? '' : 'search'), autocomplete: options.autocomplete || '',
              name: '', id: '', placeholder: '', labels: [], isContentEditable: options.contenteditable || false,
              ownerDocument: { execCommand(...args) { execCalls.push(args); return true; } },
              getAttribute(name) { return name === 'aria-label' ? options.ariaLabel || (options.href ? 'Home' : 'Search') : ''; },
              getRootNode() { return { activeElement: options.focusChanges ? null : element }; },
              focus() {}, dispatchEvent(event) { dispatched.push(event); if(options.focusChanges) this._focus = false; },
            };
            if (tagName === 'INPUT') Object.setPrototypeOf(element, InputElement.prototype);
            if (tagName === 'TEXTAREA') Object.setPrototypeOf(element, TextareaElement.prototype);
            const action = new Function('HTMLInputElement', 'HTMLTextAreaElement', 'InputEvent', `return (${declaration})`)(
              InputElement, TextareaElement, InputEvent,
            );
            if(options.focusChanges) element.getRootNode = () => ({ activeElement: element });
            const value = action.call(element, params.arguments[0].value);
            options.execCalls = execCalls;
            options.dispatched = dispatched;
            options.typedValue = element.value;
            return { result: { value } };
          }
          return { result: { value: params.objectId === 'node-99' && !options.overlap } };
        }
        if (method === 'Runtime.evaluate') {
          if (options.listenerError && params.expression === 'window') throw new Error('listener inspection unavailable');
          return { result: { objectId: params.expression } };
        }
        if (method === 'DOMDebugger.getEventListeners') {
          if (options.listenerError) throw new Error('listener inspection unavailable');
          return { listeners: options.windowListener && params.objectId === 'window'
            ? [{ type: 'click' }] : [] };
        }
        if (method === 'DOM.getBoxModel') return { model: { content: [0, 0, 100, 0, 100, 40, 0, 40] } };
        if (method === 'DOM.getNodeForLocation') {
          hitChecks += 1;
          if (options.hoverHijack && hovered && hitChecks >= 3) return { backendNodeId: 99 };
          return { backendNodeId: options.overlap ? 99 : 12 };
        }
        if (method === 'Runtime.releaseObject') return {};
        if (method === 'Input.dispatchMouseEvent' && params.type === 'mouseMoved') hovered = true;
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
    nativeHandler({ id: 1, op: options.operation || 'click', ref_node: 12, text: options.text || 'query', expected: options.expected });
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
  const reply = await new Promise(resolve => { port.postMessage = resolve; nativeHandler({ id: 2, op: 'type', ref_node: 12, text: 'hello', expected: expectedSummaries.type }); });
  assert.equal(reply.ok, false);
  assert.equal(calls.some(call => call.method === 'Input.insertText'), false);
  assert.equal(calls.some(call => call.method === 'Runtime.callFunctionOn' && /execCommand/.test(call.params.functionDeclaration || '')), true);
}

await typeFocusChanges();
const clickPositive = await scenario('positive click', {
  href: 'https://shop.example/catalog', expected: expectedSummaries.click,
});
assert.equal(clickPositive.reply.ok, true, 'Python-generated expected summary matches Chrome target');
assert.equal(clickPositive.calls.some(call => call.method === 'Input.dispatchMouseEvent' && call.params.type === 'mousePressed'), true);
const trustedButtonPositive = await scenario('positive trusted-site button', {
  tag: 'button', expected: expectedSummaries.button,
});
assert.equal(trustedButtonPositive.reply.ok, true, `ordinary button on a trusted site is not filtered by the extension: ${JSON.stringify(trustedButtonPositive.reply)}`);
assert.equal(trustedButtonPositive.calls.some(call => call.method === 'Input.dispatchMouseEvent' && call.params.type === 'mousePressed'), true,
  'trusted-site button reaches mousePressed');

const mismatchedButton = await scenario('negative trusted-site button expected mismatch', {
  tag: 'button', expected: expectedSummaries.click,
});
assert.equal(mismatchedButton.reply.ok, false, 'a mismatched expected summary still refuses the button');
assert.equal(mismatchedButton.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const invalidExpectedButton = await scenario('negative trusted-site button mismatched expected', {
  tag: 'button', expected: JSON.stringify(['button']),
});
assert.equal(invalidExpectedButton.reply.ok, false, 'a mismatched button summary is refused');
assert.equal(invalidExpectedButton.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const typePositive = await scenario('positive type', {
  operation: 'type', expected: expectedSummaries.type, text: 'query',
});
assert.equal(typePositive.reply.ok, true, 'Python-generated expected summary authorizes search typing');
assert.equal(typePositive.calls.some(call => call.method === 'Runtime.callFunctionOn'
  && /Object\.getOwnPropertyDescriptor/.test(call.params.functionDeclaration || '')), true,
  'positive type reaches native value setter');
const overlap = await scenario('negative overlap', { href: 'https://shop.example/catalog', overlap: true });
assert.equal(overlap.reply.ok, false, 'overlapping hit is refused');
assert.equal(overlap.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const changed = await scenario('negative changed href', { href: 'https://shop.example/catalog', changeHref: true,
  expected: expectedSummaries.click });
assert.equal(changed.reply.ok, false, 'changed href is refused');
assert.equal(changed.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
// An SPA (React, Vue, analytics) keeps click listeners on its root or on `window`: once the engine
// decided (trusted site or an approved card), that alone must not stop the click.
const windowListener = await scenario('positive click with a window listener', { href: 'https://shop.example/catalog',
  windowListener: true, expected: expectedSummaries.click });
assert.equal(windowListener.reply.ok, true, `a listener on window does not refuse a decided click: ${JSON.stringify(windowListener.reply)}`);
assert.equal(windowListener.calls.some(call => call.method === 'Input.dispatchMouseEvent' && call.params.type === 'mousePressed'), true);
const buttonWithListener = await scenario('positive button with a window listener', { tag: 'button', windowListener: true,
  expected: expectedSummaries.button });
assert.equal(buttonWithListener.reply.ok, true, 'a decided button click goes through on an SPA');
const noExpected = await scenario('negative click without an expected summary', { tag: 'button' });
assert.equal(noExpected.reply.ok, false, 'an action without the engine summary is refused');
assert.equal(noExpected.calls.some(call => call.method === 'Input.dispatchMouseEvent'), false);
const hoverHijack = await scenario('negative hover hijack', { href: 'https://shop.example/catalog', hoverHijack: true });
assert.equal(hoverHijack.reply.ok, false, 'hover-induced target change is refused');
assert.equal(hoverHijack.calls.some(call => call.method === 'Input.dispatchMouseEvent' && call.params.type === 'mousePressed'), false);
for (const [name, options] of [
  ['password input', { type: 'password' }],
  ['payment autocomplete', { autocomplete: 'billing cc-number' }],
  ['sensitive aria label', { ariaLabel: 'Credit card number' }],
]) {
  const result = await scenario(`negative ${name}`, options);
  assert.equal(result.reply.ok, false, `${name} is refused`);
  assert.equal(result.calls.some(call => call.method === 'Input.insertText' || call.method === 'Input.dispatchMouseEvent'), false);
}
// Listener inspection is advisory since the per-site model (plan §8): the engine decides, the extension
// checks the node is the one decided. A failed inspection neither blocks nor authorizes on its own.
const listenerError = await scenario('positive click when listener inspection fails', { href: 'https://shop.example/catalog',
  listenerError: true, expected: expectedSummaries.click });
assert.equal(listenerError.reply.ok, true, `a failed listener inspection does not change a decided click: ${JSON.stringify(listenerError.reply)}`);
console.log('service worker harness positive: decided click, trusted-site button, click and button with a window listener, click with failed listener inspection, search type through the native setter');
console.log('service worker harness negative: mismatched expected button (twice), no expected summary, overlap, changed href, hover hijack, password, cc-number, sensitive ariaLabel');
console.log('service worker harness: 6 positive + 9 negative behavioral cases passed');

const HOST = 'app.chimera.browser';
const CDP = '1.3';
let nativePort;
let pairingRequestId = 0;
const pairingPending = new Map();
let groupId;
let activeTabId;
let nextId = 1;
let requestedOrigin;
let actionsDisabled = false;
// The owner pressed Stop in the popup. Kept in chrome.storage.local (it survives the service worker being
// recycled and Chrome restarting) and cleared only by the owner's Start or a pairing from the popup, never
// by an app request or by any event that wakes the worker (ext-04).
let stopped = false;
// Bumped by every Stop, Cancel and app `close`. An operation reads it when it is admitted and checks it before
// each effect (opening a tab, navigating, clicking, typing), so a Stop that lands while an operation waits on
// Chrome (a permission check, say) drops that operation instead of letting it finish (ext, Codex review).
let stopEpoch = 0;
// Browser actions currently inside handle(); the download guard below refuses any download started meanwhile.
let actionsInFlight = 0;
const tabIds = new Set();

function safePreview(fields) {
  if (!Array.isArray(fields)) return [];
  const sensitive = /password|senha|passwort|contrase[nñ]a|mot\s+de\s+passe|passcode|secret|cvv|cvc|csc|card\s*number|security\s+code|c[oó]digo\s+de\s+seguran|n[uú]mero\s+do\s+cart[aã]o|n[uú]mero\s+de\s+tarjeta|kartennummer|\bpin\b|token|2fa|otp|c[oó]digo\s+de\s+verifica|verification\s+code/i;
  const sensitiveAutocomplete = new Set(['one-time-code', 'current-password', 'new-password']);
  let total = 0;
  const preview = [];
  for (const field of fields) {
    if (!field || typeof field !== 'object') continue;
    const type = typeof field.type === 'string' ? field.type.toLowerCase() : '';
    const label = typeof field.label === 'string' ? field.label.trim() : '';
    const value = typeof field.value === 'string' ? field.value : '';
    const autocomplete = typeof field.autocomplete === 'string' ? field.autocomplete.toLowerCase().split(/\s+/) : [];
    const rawIdentity = [label, field.name, field.id, field.ariaLabel, field['aria-labelledby'], field.labelledByText,
      field.placeholder, field.autocomplete, ...(Array.isArray(field.ancestors) ? field.ancestors : [])]
      .filter(item => typeof item === 'string').join(' ');
    const identity = rawIdentity.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replace(/[-_.]/g, ' ');
    const digits = value.replace(/\D/g, '');
    let sum = 0, doubleDigit = false;
    for (let i = digits.length - 1; i >= 0; i--) {
      let digit = Number(digits[i]);
      if (doubleDigit) { digit *= 2; if (digit > 9) digit -= 9; }
      sum += digit; doubleDigit = !doubleDigit;
    }
    const compact = value.replace(/[\s-]/g, '');
    const cardNumber = digits.length >= 13 && digits.length <= 19 && digits.length === compact.length && sum % 10 === 0;
    const phone = /phone|telephone|mobile|tel[eé]fono|celular|telefone/i.test(identity);
    const phoneOrId = !phone && (
      /^\d{3}[. -]?\d{3}[. -]?\d{3}-?\d{2}$/.test(value.trim())
      || (digits.length === 11 && !/^(\d)\1{10}$/.test(digits))
    );
    if (['hidden', 'password', 'file'].includes(type) || autocomplete.some(token => token.startsWith('cc-') || sensitiveAutocomplete.has(token)) || /cc\s+(?:number|csc|exp|name)/i.test(identity) || sensitive.test(identity) || cardNumber || phoneOrId) continue;
    if (/character\s*count|characters?\s*(remaining|left)|\bchars?\b/i.test(identity)) continue;
    if (!label || !value.trim()) continue;
    const item = { ...field, label: label.slice(0, 120), value: value.slice(0, 300) };
    const size = item.label.length + item.value.length;
    if (preview.length >= 8 || total + size > 1500) break;
    preview.push(item);
    total += size;
  }
  return preview;
}

// Tab bookkeeping is per browser session. The two owner switches (Stop and the debugging bar's Cancel) are
// not: they live in storage.local so a Chrome restart cannot turn the agent back on (ext-04, ext-08).
async function persistState() {
  await chrome.storage.session.set({
    chimeraGroupId: groupId,
    chimeraTabIds: [...tabIds],
    chimeraActiveTabId: activeTabId,
    chimeraRequestedOrigin: requestedOrigin,
  });
  await chrome.storage.local.set({ chimeraActionsDisabled: actionsDisabled, chimeraStopped: stopped });
}

async function restoreState() {
  const saved = await chrome.storage.session.get([
    'chimeraGroupId', 'chimeraTabIds', 'chimeraActiveTabId', 'chimeraRequestedOrigin',
  ]);
  const owner = await chrome.storage.local.get(['chimeraActionsDisabled', 'chimeraStopped']);
  groupId = saved.chimeraGroupId;
  tabIds.clear();
  for (const tabId of saved.chimeraTabIds || []) tabIds.add(tabId);
  activeTabId = saved.chimeraActiveTabId;
  requestedOrigin = saved.chimeraRequestedOrigin;
  actionsDisabled = owner.chimeraActionsDisabled === true;
  stopped = owner.chimeraStopped === true;
}

// Pure decisions, kept apart from the chrome.* calls so they can be tested on their own.
// May the worker open (or keep) its link to the app? Never while the owner's Stop holds.
function mayConnect(ownerStopped) { return ownerStopped !== true; }

// Is this paused non-navigation request a speculative prefetch? Chrome marks them with a purpose header
// (`Sec-Purpose: prefetch` for speculation rules, `Purpose: prefetch` for <link rel=prefetch>); the event may
// also report the type as 'Prefetch'. Which of these the event really carries is checked by hand (REVISAO.md).
function isPrefetchRequest(params) {
  if (params && params.resourceType === 'Prefetch') return true;
  const headers = (params && params.request && params.request.headers) || {};
  return Object.keys(headers).some(name => /^(sec-)?purpose$/i.test(name) && /prefetch/i.test(String(headers[name])));
}

// The origin a navigation lands on, or null when it is not a plain http(s) page. Anything else (a
// chrome:// page, a data: or blob: document, a javascript: URL) is refused rather than guessed at.
function navigationOrigin(url) {
  try {
    const parsed = new URL(url || '');
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return null;
    return parsed.origin;
  } catch { return null; }
}

function setStatus(text) {
  chrome.storage.session.set({ chimeraStatus: text });
}

// Only http(s) pages are ever debugged. Allowlisting the scheme (rather than blocklisting chrome://,
// chrome-extension://, edge://, about:) also covers the new-tab page and about:blank, which the debugger
// refuses with "Cannot access a chrome:// URL" and which a fresh tab starts on.
function isWebUrl(url) {
  return /^https?:\/\//i.test(url || '');
}

const NOT_A_WEB_TAB = 'The Chimera tab is not on a website; navigate to a site first';

async function groupIsOpen() {
  try { await chrome.tabGroups.get(groupId); return true; }
  catch { return false; }
}

const STOPPED_MESSAGE = 'Chimera Browser was stopped; the operation was dropped before it changed anything';

// Every Chimera tab starts on about:blank. The debugger is attached and the request filter is on BEFORE any
// URL loads, so even the first navigation, and every redirect after it, is checked before it leaves. Opening
// the tab on the URL itself let that first request go out unprotected.
async function createBlankTab(live) {
  live();
  const tab = await chrome.tabs.create({ active: true, url: 'about:blank' });
  try { live(); } catch (error) {
    await chrome.tabs.remove(tab.id).catch(() => {});
    throw error;
  }
  return tab;
}

async function openTabInGroup(live) {
  const tab = await createBlankTab(live);
  tabIds.add(tab.id);
  activeTabId = tab.id;
  await chrome.tabs.group({ tabIds: [tab.id], groupId });
  await persistState();
}

// `openUrl` is set only for navigate (the URL itself is loaded later, by Page.navigate, once the tab is
// protected). Reuse the group's own tab when it is a web page; otherwise open a new tab in the group. Never
// touches a tab outside the group (the owner's focused tab, for one).
async function ensureGroup(openUrl, live) {
  live();
  if (groupId !== undefined && await groupIsOpen()) {
    const tab = activeTabId === undefined ? null : await chrome.tabs.get(activeTabId).catch(() => null);
    if (tab && tab.groupId === groupId) {
      if (isWebUrl(tab.url) || openUrl === undefined) return;
      return openTabInGroup(live);
    }
  }
  if (openUrl === undefined) throw new Error('No Chimera tab is active; navigate to a site first');
  groupId = undefined;
  activeTabId = undefined;
  const tab = await createBlankTab(live);
  tabIds.add(tab.id);
  activeTabId = tab.id;
  groupId = await chrome.tabs.group({ tabIds: [tab.id] });
  await chrome.tabGroups.update(groupId, { title: 'Chimera', collapsed: false });
  await persistState();
}

// Current Chrome refuses this deprecated browser-level command on an extension's debugger session
// ("Cannot not access browser-level commands", seen live 26/10), so it used to fail every action. Denying
// downloads here is a first line only; the downloads listener below is the guard that actually holds.
async function denyDownloads(tabId) {
  try { await chrome.debugger.sendCommand({ tabId }, 'Page.setDownloadBehavior', { behavior: 'deny' }); }
  catch (error) {
    if (!/browser-level commands/.test(String(error))) throw error;
  }
}

// Request types held at the Request stage. The names are the ones Chromium's Fetch.enable ACCEPTS (its filter
// parser, content/browser/devtools/protocol/fetch_handler.cc), which is narrower than the protocol's
// Network.ResourceType: 'Prefetch' is rejected there with "Unknown resource type in fetch filter".
//  - Document: every navigation (typed, redirected, history, script, link).
//  - Fetch: the interceptor maps it to both fetch()/XHR and speculative prefetch (kXhr + kPrefetch). Ordinary
//    fetch/XHR is let straight through by onFetchPaused; only a request that looks like a prefetch is held to
//    the grant (see isPrefetchRequest). This costs one round trip per page fetch/XHR.
// `Other` is not listed: favicons, beacons and the like, which the page may send anywhere; holding them to the
// grant would break ordinary pages without protecting anything model-supplied.
const FETCH_PATTERNS = [
  { urlPattern: '*', resourceType: 'Document', requestStage: 'Request' },
  { urlPattern: '*', resourceType: 'Fetch', requestStage: 'Request' },
];
// Every child target (a popup, a prerendered page, an out-of-process iframe, a worker) starts paused and is
// handed to onTargetAttached, which protects it before it is allowed to run.
const AUTO_ATTACH = { autoAttach: true, waitForDebuggerOnStart: true, flatten: true };
// Requests held at Fetch.requestPaused, by id, so Stop can fail each one before anything is detached.
const paused = new Map();
// Protection failures the owner should see in the popup. They are reported, never swallowed.
const containmentIssues = [];

function sessionTarget(source) {
  return source.sessionId ? { tabId: source.tabId, sessionId: source.sessionId } : { tabId: source.tabId };
}

function sendTo(target, method, params = {}) { return chrome.debugger.sendCommand(target, method, params); }

// Turn the protection on for one debugger session. `type` is 'page' for the tab itself and for popups and
// prerendered pages (held to the grant), anything else only watches for its own children.
async function protectTarget(target, type) {
  if (type === 'page') {
    // Every top-level document request pauses until onFetchPaused has checked where it goes: the one place
    // that sees redirects, history navigation, link clicks and script navigation before the request leaves.
    await sendTo(target, 'Fetch.enable', { patterns: FETCH_PATTERNS });
    // A service worker registered by the page could answer a navigation outside the filter above. Chromium
    // ignores the bypass override on a session whose Network domain is off, so Network is enabled first.
    await sendTo(target, 'Network.enable');
    await sendTo(target, 'Network.setBypassServiceWorker', { bypass: true });
    try { await sendTo(target, 'Page.setPrerenderingAllowed', { isAllowed: false }); }
    catch (error) {
      // Older Chrome has no such command; the prerendered page is then held by the auto-attach below.
      if (!/wasn't found|not found|not supported/i.test(String(error))) throw error;
    }
  }
  await sendTo(target, 'Target.setAutoAttach', AUTO_ATTACH);
}

async function attach(tabId) {
  try { await chrome.debugger.attach({ tabId }, CDP); }
  catch (error) {
    if (!String(error).includes('Another debugger is already attached')) throw error;
  }
  await denyDownloads(tabId);
  await protectTarget({ tabId }, 'page');
}

const NOT_GRANTED = 'This site has not been granted to the Chimera extension';
// Callers waiting on a load learn at once that the navigation was refused instead of timing out.
const navigationRefusals = new Set();
let lastRefusal = '';

async function isGranted(origin) {
  return chrome.permissions.contains({ origins: [`${origin}/*`] });
}

async function isGrantedUrl(url) {
  const origin = navigationOrigin(url);
  return origin !== null && await isGranted(origin);
}

// Throws NOT_GRANTED, and records the origin so the popup offers "Allow", when `url` is not on a granted origin.
async function requireGrantedUrl(url) {
  const origin = navigationOrigin(url);
  if (origin === null) throw new Error('Only http(s) navigation is supported');
  if (!await isGranted(origin)) {
    requestedOrigin = origin;
    await chrome.storage.session.set({ chimeraRequestedOrigin: origin });
    throw new Error(NOT_GRANTED);
  }
}

// A request that could not be failed would leave this tab free to send it. Close the tab, say so.
async function containFailure(target, requestId, failure) {
  containmentIssues.push(`Request ${requestId} could not be blocked (${failure}); its tab was closed`);
  await chrome.tabs.remove(target.tabId).catch(error => containmentIssues.push(`Tab ${target.tabId} could not be closed: ${error}`));
}

async function onFetchPaused(source, params) {
  const target = sessionTarget(source);
  const requestId = params.requestId;
  const epoch = stopEpoch;
  const url = params.request && params.request.url;
  paused.set(requestId, target);
  let refusal = null;
  let stillMine = true;
  try {
    if (stopped) throw new Error(STOPPED_MESSAGE);
    if (params.resourceType !== 'Document') {
      // fetch/XHR/prefetch. Only a prefetch is held to the grant, and never as a reason to prompt the owner.
      if (isPrefetchRequest(params) && !await isGrantedUrl(url)) throw new Error(NOT_GRANTED);
    } else {
      const mainFrame = (await sendTo(target, 'Page.getFrameTree')).frameTree.frame.id;
      // Embedded frames (captcha, payment, video) are not navigations of the page; the app already treats a
      // frame from another host as a reason to ask. Only the top-level document is held to the grant.
      if (params.frameId === mainFrame) await requireGrantedUrl(url);
    }
    if (epoch !== stopEpoch || stopped) throw new Error(STOPPED_MESSAGE);
    await sendTo(target, 'Fetch.continueRequest', { requestId });
  } catch (error) {
    // Fail closed: a refused origin, and any error while deciding, stop the request before it is sent.
    refusal = String(error.message || error);
  } finally {
    // False when Stop (or a tab leaving the group) already took this request over and dealt with it.
    stillMine = paused.delete(requestId);
  }
  if (refusal === null) return;
  lastRefusal = refusal;
  if (!stillMine) return;
  try { await sendTo(target, 'Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }); }
  catch (failure) { await containFailure(target, requestId, failure); }
  if (params.resourceType === 'Document') for (const refuse of [...navigationRefusals]) refuse(refusal);
}

// A child target the PAGE session delivers: a prerendered page, an out-of-process iframe, a worker. It arrives
// paused; it is protected first and only then allowed to run. A page that cannot be protected is closed and
// never resumed. This does NOT cover a popup (window.open, target=_blank): Chromium pauses those only for
// browser-level sessions, which chrome.debugger cannot open. See the tabs.onCreated note below.
async function onTargetAttached(source, params) {
  const parent = sessionTarget(source);
  const child = { tabId: source.tabId, sessionId: params.sessionId };
  const info = params.targetInfo || {};
  try {
    if (info.type === 'page' || info.type === 'iframe') await protectTarget(child, info.type);
  } catch (error) {
    if (info.type === 'page') {
      containmentIssues.push(`A page opened from a Chimera tab could not be protected and was closed: ${error}`);
      await sendTo(parent, 'Target.closeTarget', { targetId: info.targetId })
        .catch(closeError => containmentIssues.push(`It could not be closed either: ${closeError}`));
      return;
    }
  }
  if (params.waitingForDebugger) {
    await sendTo(child, 'Runtime.runIfWaitingForDebugger')
      .catch(error => containmentIssues.push(`A child target could not be resumed: ${error}`));
  }
}

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!source || typeof source.tabId !== 'number') return;
  if (method === 'Fetch.requestPaused') void onFetchPaused(source, params || {});
  else if (method === 'Target.attachedToTarget') void onTargetAttached(source, params || {});
});

// A tab the page opens is useless to the driver (it only ever follows its own tab), so it is closed as soon as
// Chrome reports it. THIS IS NOT A GUARD AGAINST THE FIRST REQUEST: the popup's navigation (and its redirects)
// starts in the browser process in parallel with this event, so a request can leave before the tab is closed.
// No extension API available here closes that window (documented in docs/chrome-extension.md).
chrome.tabs.onCreated.addListener(async tab => {
  if (tab.openerTabId === undefined) return;
  const saved = await chrome.storage.session.get('chimeraTabIds');
  if (!(saved.chimeraTabIds || []).includes(tab.openerTabId)) return;
  await chrome.tabs.remove(tab.id)
    .catch(error => containmentIssues.push(`Tab ${tab.id} opened by a Chimera tab could not be closed: ${error}`));
});

// A tab the owner drags out of the Chimera group is the owner's again: stop filtering it and let go of it, so
// the filter never holds up their own navigation.
chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (changeInfo.groupId === undefined) return;
  void releaseIfLeftGroup(tabId, changeInfo.groupId);
});

async function releaseIfLeftGroup(tabId, newGroupId) {
  await restoreState();
  if (!tabIds.has(tabId) || newGroupId === groupId) return;
  for (const [requestId, target] of [...paused]) if (target.tabId === tabId) paused.delete(requestId);
  try { await chrome.debugger.sendCommand({ tabId }, 'Fetch.disable'); } catch { /* not attached */ }
  try { await chrome.debugger.detach({ tabId }); } catch { /* not attached */ }
  tabIds.delete(tabId);
  if (activeTabId === tabId) activeTabId = undefined;
  await persistState();
}

function originOf(url) {
  try {
    const origin = new URL(url || '').origin;
    return origin === 'null' ? '' : origin;
  } catch { return ''; }
}

// Fallback for the denial above, which Chrome may refuse. A download is cancelled and erased from history
// when it starts during a browser action, or when its URL or referrer is on the Chimera tab's origin (a
// click can start a download after handle() has already returned). The owner's own downloads elsewhere pass.
chrome.downloads.onCreated.addListener(async item => {
  let blocked = actionsInFlight > 0;
  if (!blocked) {
    const saved = await chrome.storage.session.get('chimeraActiveTabId');
    const tab = saved.chimeraActiveTabId === undefined ? null : await chrome.tabs.get(saved.chimeraActiveTabId).catch(() => null);
    const tabOrigin = originOf(tab?.url);
    blocked = tabOrigin !== '' && [item.url, item.referrer].some(url => originOf(url) === tabOrigin);
  }
  if (!blocked) return;
  await chrome.downloads.cancel(item.id).catch(() => {});
  await chrome.downloads.erase({ id: item.id }).catch(() => {});
});

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
  let nodeFrameUrl = '';
  const parents = [];
  function seek(candidate, lineage, frameUrl) {
    if (candidate.backendNodeId === refNode) {
      node = candidate;
      nodeFrameUrl = frameUrl;
      parents.push(...lineage);
      return true;
    }
    const next = [...lineage, candidate];
    for (const child of [...(candidate.children || []), ...(candidate.shadowRoots || [])])
      if (seek(child, next, frameUrl)) return true;
    if (candidate.contentDocument && seek(candidate.contentDocument, next, candidate.contentDocument.documentURL || frameUrl)) return true;
    return false;
  }
  seek(document.root, [], document.root.documentURL || '');
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
  const frameById = new Map();
  function collectFrames(frameNode) {
    if (frameNode && frameNode.frame) {
      const url = frameNode.frame.url || '';
      frameUrls.push(url);
      frameById.set(frameNode.frame.id, url);
    }
    for (const child of (frameNode && frameNode.childFrames) || []) collectFrames(child);
  }
  collectFrames(frameTree.frameTree);
  const frame = [...frameUrls, ...ancestors.map(item => `${item.src || ''} ${item.title || ''} ${item.name || ''}`)].join(' ');
  const targetFrameUrl = frameById.get(targetNode.frameId || node.frameId) || nodeFrameUrl || frameTree.frameTree.frame.url || '';
  const label = ancestors.find(item => item.tag === 'label');
  const tag = String(node.nodeName).toLowerCase();
  const ownerForm = await cdp('Runtime.callFunctionOn', {
    objectId: (await cdp('DOM.resolveNode', { backendNodeId: refNode })).object.objectId,
    functionDeclaration: 'function(){const owned=this.form; if(owned) return {method:(owned.method||"get").toLowerCase(),action:owned.action||""}; let n=this; while(n){if(n.tagName && n.tagName.toLowerCase()==="form") return {method:(n.method||"get").toLowerCase(),action:n.action||""}; n=n.parentNode || (n.getRootNode && n.getRootNode().host)} return null}',
    returnByValue: true,
  }).catch(() => null);
  const formValue = ownerForm && ownerForm.result ? ownerForm.result.value : null;
  const form = formValue ? { method: formValue.method, action: formValue.action } : null;
  const formNode = parents.find(parent => String(parent.nodeName || '').toLowerCase() === 'form');
  const formAttrs = formNode ? attrsOf(formNode) : {};
  let initialValueLength = 0;
  if (tag === 'input' || tag === 'textarea') {
    const initialValue = await cdp('Runtime.callFunctionOn', {
      objectId: (await cdp('DOM.resolveNode', { backendNodeId: refNode })).object.objectId,
      functionDeclaration: 'function(){return String(this.value || "").length}', returnByValue: true,
    }).catch(() => null);
    if (initialValue && initialValue.result) initialValueLength = Number(initialValue.result.value) || 0;
  }
  const description = {
    tag,
    type: attrs.type || '',
    autocomplete: attrs.autocomplete || '',
    name: attrs.name || '',
    id: attrs.id || '',
    ariaLabel: attrs['aria-label'] || '',
    role: attrs.role || '',
    label: label ? attrsOf(label).value || attrsOf(label)['aria-label'] || '' : '',
    accessibleName: attrs['aria-label'] || attrs.alt || attrs.title || '',
    axName: '',
    placeholder: attrs.placeholder || '',
    labels: ancestors.filter(item => item.tag === 'label').map(item => attrsOf(item).ariaLabel || ''),
    text: node.nodeValue || node.textContent || '',
    value: '',
    href: attrs.href || '',
    download: Object.prototype.hasOwnProperty.call(attrs, 'download'),
    target: attrs.target || '',
    ping: attrs.ping || '',
    hasForm: Boolean(form),
    formMethod: form ? form.method : formAttrs.method || '',
    formMethodOverride: attrs.formmethod || '',
    formActionOverride: attrs.formaction || '',
    autocomplete: attrs.autocomplete || '',
    type: attrs.type || '',
    formTargetOverride: attrs.formtarget || '',
    valueLength: initialValueLength,
    forFileInput: false,
    forSubmitInput: false,
    hasClickListeners: null,
    formAction: form ? form.action : formAttrs.action || '',
    frame,
    frameUrl: targetFrameUrl,
  };
  const ax = await cdp('Accessibility.getPartialAXTree', { backendNodeId: refNode, fetchRelatives: true });
  const axNode = (ax.nodes || []).find(item => item.backendDOMNodeId === refNode);
  description.axName = axNode && axNode.name ? axNode.name.value || '' : '';
  description.accessibleName = description.axName || description.accessibleName;
  const resolvedTarget = await cdp('DOM.resolveNode', { backendNodeId: refNode });
  const objectId = resolvedTarget.object.objectId;
  try {
    const previewResult = await cdp('Runtime.callFunctionOn', {
    objectId: resolvedTarget.object.objectId,
    functionDeclaration: `function(){
      ${safePreview.toString()}
      const visible=e=>!!(e.getClientRects().length && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none');
      const controlsIn=el=>Array.from(el.querySelectorAll('input,textarea,select,[contenteditable]:not([contenteditable="false"])'));
      let container=null, nearest=null, n=this, depth=0;
      while(n && n!==document && depth++<15){
        const controls=controlsIn(n).filter(visible);
        if(controls.length && !nearest) nearest=n;
        if(n.tagName && n.tagName.toLowerCase()==='form' && controls.length){container=n;break;}
        if(n.getAttribute && n.getAttribute('role')==='dialog' && controls.length){container=n;break;}
        n=n.parentElement;
      }
      if(!container) container=nearest;
      if(!container){
        let c=this;for(let i=0;c&&c!==document&&i<15;i++,c=c.parentElement){
          if((c.innerText||'').trim()){
            const text=(c.getAttribute&&c.getAttribute('aria-label'))||(c.innerText||'').trim().slice(0,300);
            return safePreview([{label:'context',value:String(text).slice(0,300)}]);
          }
        }
        return [];
      }
      const fields=[];
      for(const e of controlsIn(container)){
        if(!visible(e)) continue;
        const tag=e.tagName.toLowerCase(), type=(e.type||e.getAttribute('type')||'').toLowerCase();
        const inherited=[];let ancestor=e.parentElement;
        for(let i=0;ancestor&&i<15;i++,ancestor=ancestor.parentElement){
          const ac=ancestor.getAttribute&&ancestor.getAttribute('autocomplete');
          const ty=ancestor.getAttribute&&ancestor.getAttribute('type');
          if(ac) inherited.push(ac);if(ty) inherited.push(ty);
        }
        const autocomplete=(e.getAttribute('autocomplete')||'').toLowerCase();
        const labelledByText=(e.getAttribute('aria-labelledby')||'').split(/\\s+/).map(id=>e.ownerDocument.getElementById(id)).filter(Boolean).map(x=>x.innerText||x.textContent||'').join(' ');
        const label=(e.getAttribute('aria-label')||labelledByText||Array.from(e.labels||[]).map(x=>x.innerText||x.textContent||'').join(' ')||e.getAttribute('placeholder')||e.name||e.id||'').trim();
        const value=tag==='select' ? Array.from(e.selectedOptions||[]).map(x=>x.textContent||'').join(', ') : (e.isContentEditable ? e.innerText : e.value);
        fields.push({label,value,type,autocomplete:[autocomplete,...inherited].join(' '),name:e.name||'',id:e.id||'',ariaLabel:e.getAttribute('aria-label')||'',labelledByText,placeholder:e.getAttribute('placeholder')||'',ancestors:inherited});
      }
      const chips=Array.from(container.querySelectorAll('[email],[data-hovercard-id]'));
      const recipients=new Set(fields.map(field=>String(field.value||'').trim().toLowerCase()).filter(value=>/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(value)));
      for(const chip of chips){if(!visible(chip)||chip.getAttribute('role')==='option')continue;const value=(chip.getAttribute('email')||chip.getAttribute('data-hovercard-id')||chip.innerText||chip.textContent||'').trim();const key=value.toLowerCase();if(value&&!recipients.has(key)){fields.push({label:'recipient',value});if(/^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$/.test(key))recipients.add(key);}}
      const preview=safePreview(fields);
      if(preview.length)return preview;
      const context=(container.getAttribute&&container.getAttribute('aria-label'))||(container.innerText||'').trim().slice(0,300);
      return context ? safePreview([{label:'context',value:String(context).slice(0,300)}]) : [];
    }`,
    returnByValue: true,
  }).catch(() => null);
    description.preview = previewResult && previewResult.result && Array.isArray(previewResult.result.value)
      ? previewResult.result.value : [];
    const labels = await cdp('Runtime.callFunctionOn', {
    objectId: resolvedTarget.object.objectId,
    functionDeclaration: 'function(){const labels=this.labels ? Array.from(this.labels) : []; const ids=(this.getAttribute("aria-labelledby")||"").split(/\\s+/); const named=ids.map(id=>{const e=this.ownerDocument.getElementById(id);return e?(e.innerText||e.textContent||""):""}).filter(Boolean); return {labels:labels.map(x=>x.innerText||x.textContent||"").concat(named).filter(Boolean), named:named.join(" ")}}',
    returnByValue: true,
  }).catch(() => null);
  if (labels && labels.result && labels.result.value) {
    description.labels.push(...(labels.result.value.labels || []));
    if (labels.result.value.named) description.label = labels.result.value.named;
  }
  description.ariaLabel = attrs['aria-label'] || '';
  if (attrs['aria-labelledby']) {
    const labelled = await cdp('Runtime.evaluate', {
      expression: `(${JSON.stringify(attrs['aria-labelledby'].split(/\s+/))}).map(id => {const el=document.getElementById(id); return el ? (el.innerText || el.textContent || '') : ''}).join(' ')`,
      returnByValue: true,
    }).catch(() => null);
    if (labelled && labelled.result && labelled.result.value) description.labels.push(labelled.result.value);
  }
  if (tag === 'label') {
    const labelData = await cdp('Runtime.callFunctionOn', {
      objectId: (await cdp('DOM.resolveNode', { backendNodeId: refNode })).object.objectId,
      functionDeclaration: 'function(){const e=this.control || (this.htmlFor ? this.ownerDocument.getElementById(this.htmlFor) : null); return {text:this.innerText||this.textContent||"", file:Boolean(e && e.tagName.toLowerCase()==="input" && e.type==="file"), submit:Boolean(e && ((e.tagName.toLowerCase()==="input" && ["submit","image"].includes(e.type)) || (e.tagName.toLowerCase()==="button" && (!e.type || e.type==="submit"))))}}',
      returnByValue: true,
    }).catch(() => null);
    const value = labelData && labelData.result ? labelData.result.value : null;
    description.forFileInput = Boolean(value && value.file);
    description.forSubmitInput = Boolean(value && value.submit);
    if (value && value.text) description.label = value.text;
  }
  description.hasForm = Boolean(form || parents.some(parent => String(parent.nodeName || '').toLowerCase() === 'form'));
  if (tag === 'a' && attrs.href) {
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    const hrefResult = await cdp('Runtime.callFunctionOn', {
      objectId: resolved.object.objectId,
      functionDeclaration: 'function(){return this.href || ""}', returnByValue: true,
    });
    description.href = hrefResult.result.value || '';
  }
  const listenerTypes = new Set([
    'click', 'auxclick', 'mousedown', 'mouseup', 'pointerdown', 'pointerup',
    'touchstart', 'touchend', 'keydown', 'keyup', 'submit',
  ]);
  description.hasEventListeners = false;
  try {
    for (const candidate of [node, ...parents]) {
      if (!candidate.backendNodeId) throw new Error('Listener ancestry could not be verified');
      const resolved = await cdp('DOM.resolveNode', { backendNodeId: candidate.backendNodeId });
      const listeners = await cdp('DOMDebugger.getEventListeners', {
        objectId: resolved.object.objectId, depth: 0, pierce: true,
      });
      await cdp('Runtime.releaseObject', { objectId: resolved.object.objectId }).catch(() => {});
      if ((listeners.listeners || []).some(listener => listenerTypes.has(String(listener.type || '').toLowerCase()))) {
        description.hasEventListeners = true;
        break;
      }
    }
    for (const expression of ['document', 'window']) {
      const evaluated = await cdp('Runtime.evaluate', { expression, returnByValue: false });
      const objectId = evaluated.result && evaluated.result.objectId;
      if (!objectId) throw new Error('Listener target unavailable');
      const listeners = await cdp('DOMDebugger.getEventListeners', { objectId, depth: 0, pierce: true });
      await cdp('Runtime.releaseObject', { objectId }).catch(() => {});
      if ((listeners.listeners || []).some(listener => listenerTypes.has(String(listener.type || '').toLowerCase()))) {
        description.hasEventListeners = true;
        break;
      }
    }
  } catch {
    description.hasEventListeners = null;
  }
  return description;
  } finally {
    await cdp('Runtime.releaseObject', { objectId }).catch(() => {});
  }
}

function protectedTarget(target) {
  const autocompleteTokens = String(target.autocomplete || '').toLowerCase().split(/\s+/);
  const identity = [target.name, target.id, target.label, target.ariaLabel, target.placeholder,
    target.accessibleName, target.axName, target.type, ...(target.labels || [])].filter(Boolean).join(' ');
  return (target.tag === 'input' && String(target.type).toLowerCase() === 'password')
    || autocompleteTokens.some(token => token.startsWith('cc-') ||
      ['one-time-code', 'current-password', 'new-password'].includes(token))
    || /password|senha|passwort|contrase[nñ]a|mot\s+de\s+passe|passcode|secret|cvv|cvc|csc|card\s*number|n[uú]mero\s+do\s+cart[aã]o|n[uú]mero\s+de\s+tarjeta|kartennummer|\bpin\b|token|2fa|otp|c[oó]digo\s+de\s+verifica|verification\s+code/i.test(identity)
    || /stripe|adyen|pagseguro|mercadopago|paypal/i.test(target.frameUrl || '');
}

function targetSummary(target) {
  return JSON.stringify([
    target.tag || '', target.type || '', target.autocomplete || '', target.axName || target.accessibleName || '',
    target.href || '', target.target || '', Boolean(target.download), Boolean(target.hasForm),
    target.formMethod || '', target.ping || '', target.frameUrl || '',
    Boolean(target.forSubmitInput), target.formMethodOverride || '', target.formActionOverride || '',
    target.formTargetOverride || '', Number(target.valueLength) || 0,
  ]);
}

function expectedMatches(target, expected) {
  if (typeof expected !== 'string') return false;
  try { return targetSummary(target) === JSON.stringify(JSON.parse(expected)); }
  catch { return false; }
}

async function actOnTarget(operation, refNode, text = '', expected = null, expectedPreview = null, live = () => {}) {
  await checkCurrentOrigin();
  const target = await describeTarget(refNode);
  // Every click/type carries the summary the engine decided on; an action without one is refused, not trusted.
  if (!expectedMatches(target, expected)) throw new Error('Target changed while approval was pending');
  if (protectedTarget(target)) throw new Error('Sensitive field is blocked by the extension');
  const tab = await chrome.tabs.get(activeTabId);
  let host = '';
  try { host = new URL(tab.url).hostname; } catch { throw new Error('Current page has no permitted web origin'); }

  let replacedLength = 0;
  let typed = null;
  if (operation === 'click') {
    const approvedFieldsRemain = (fields, approved) => {
      const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
      const comparable = items => items.filter(field => {
        if (!field || typeof field !== 'object' || field.label === 'context' || field.role === 'option') return false;
        const identity = [field.label, field.name, field.id, field.ariaLabel, field.placeholder,
          field.labelledByText, field.autocomplete, ...(Array.isArray(field.ancestors) ? field.ancestors : [])]
          .filter(value => typeof value === 'string').join(' ');
        const value = normalize(field.value);
        const digits = value.replace(/\D/g, '');
        const compact = value.replace(/[\s-]/g, '');
        const luhn = digits.length >= 13 && digits.length <= 19 && digits.length === compact.length
          && (() => { let sum = 0; let alternate = false; for (let i = digits.length - 1; i >= 0; i--) {
            let digit = Number(digits[i]); if (alternate) { digit *= 2; if (digit > 9) digit -= 9; }
            sum += digit; alternate = !alternate;
          } return sum % 10 === 0; })();
        const counter = /character\s*count|characters?\s*(remaining|left)|\bchars?\b/i.test(identity);
        const phoneOrId = !/phone|telephone|mobile|tel[eé]fono|celular|telefone/i.test(identity)
          && !counter
          && (/^\d{3}[. -]?\d{3}[. -]?\d{3}-?\d{2}$/.test(value)
            || (digits.length === 11 && !/^(\d)\1{10}$/.test(digits)));
        const sensitive = /password|senha|passwort|contrase[nñ]a|mot\\s+de\\s+passe|passcode|secret|cvv|cvc|csc|card\\s*number|n[uú]mero\\s+do\\s+cart[aã]o|n[uú]mero\\s+de\\s+tarjeta|kartennummer|\\bpin\\b|token|2fa|otp|c[oó]digo\\s+de\\s+verifica|verification\\s+code|cc-number|cc-csc|cc-exp|cc-name/i.test(identity)
          || /(^|\\s)(cc-|one-time-code|current-password|new-password)(\\s|$)/i.test(field.autocomplete || '')
          || ['password', 'hidden', 'file'].includes(String(field.type || '').toLowerCase());
        return Boolean(value) && !luhn && !phoneOrId && !sensitive && !counter;
      });
      const current = comparable(fields);
      const approvedFields = comparable(approved);
      if (!approvedFields.length) return true;
      const matches = (field, candidate) => normalize(field.label) === normalize(candidate.label)
        && normalize(field.value) === normalize(candidate.value);
      return approvedFields.every(expectedField => current.some(field => matches(field, expectedField)))
        && current.every(field => approvedFields.some(expectedField => matches(field, expectedField)));
    };
    if (Array.isArray(expectedPreview) && !approvedFieldsRemain(target.preview || [], expectedPreview))
      throw new Error('approval_preview_changed');
    const freshTarget = await describeTarget(refNode);
    if (Array.isArray(expectedPreview) && !approvedFieldsRemain(freshTarget.preview || [], expectedPreview))
      throw new Error('approval_preview_changed');
    if (targetSummary(freshTarget) !== targetSummary(target) || protectedTarget(freshTarget))
      throw new Error('Target changed before click; refusing to click');
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    if (!resolved.object || !resolved.object.objectId) throw new Error('Target node is unavailable');
    const model = await cdp('DOM.getBoxModel', { objectId: resolved.object.objectId });
    const quad = model.model && model.model.content;
    if (!quad || quad.length < 8) throw new Error('Target is not clickable');
    const points = [
      [(quad[0] + quad[2] + quad[4] + quad[6]) / 4, (quad[1] + quad[3] + quad[5] + quad[7]) / 4],
      [(quad[0] * 0.8 + quad[2] * 0.1 + quad[4] * 0.1), (quad[1] * 0.8 + quad[3] * 0.1 + quad[5] * 0.1)],
      [(quad[0] * 0.1 + quad[2] * 0.8 + quad[6] * 0.1), (quad[1] * 0.1 + quad[3] * 0.8 + quad[7] * 0.1)],
      [(quad[0] * 0.1 + quad[4] * 0.8 + quad[6] * 0.1), (quad[1] * 0.1 + quad[5] * 0.8 + quad[7] * 0.1)],
      [(quad[2] * 0.1 + quad[4] * 0.1 + quad[6] * 0.8), (quad[3] * 0.1 + quad[5] * 0.1 + quad[7] * 0.8)],
    ];
    let selectedPoint = null;
    let hitBackendId;
    for (const point of points) {
      const hit = await cdp('DOM.getNodeForLocation', {
        x: Math.round(point[0]), y: Math.round(point[1]), includeUserAgentShadowDOM: true,
      });
      if (hit.backendNodeId === refNode) { selectedPoint = point; hitBackendId = hit.backendNodeId; break; }
      if (!hitBackendId) hitBackendId = hit.backendNodeId;
    }

    const [x, y] = selectedPoint || points[0];
    if (!hitBackendId) throw new Error('No safe point under target');
    if (hitBackendId !== refNode) {
      const resolvedTarget = await cdp('DOM.resolveNode', { backendNodeId: refNode });
      const resolvedHit = await cdp('DOM.resolveNode', { backendNodeId: hitBackendId });
      const isDescendant = await cdp('Runtime.callFunctionOn', {
        objectId: resolvedHit.object.objectId,
        functionDeclaration: 'function(target){let n=this; while(n){if(n===target)return true; n=n.parentNode || (n.getRootNode && n.getRootNode().host)} return false}',
        arguments: [{ objectId: resolvedTarget.object.objectId }], returnByValue: true,
      });
      await cdp('Runtime.releaseObject', { objectId: resolvedTarget.object.objectId }).catch(() => {});
      await cdp('Runtime.releaseObject', { objectId: resolvedHit.object.objectId }).catch(() => {});
      if (isDescendant.result.value !== true)
        throw new Error('The element under the click changed; refusing to click');
    }
    const finalTarget = await describeTarget(refNode);
    if (targetSummary(finalTarget) !== targetSummary(target) || protectedTarget(finalTarget))
      throw new Error('Target changed before click; refusing to click');
    const freshHit = await cdp('DOM.getNodeForLocation', {
      x: Math.round(x), y: Math.round(y), includeUserAgentShadowDOM: true,
    });
    if (freshHit.backendNodeId !== hitBackendId)
      throw new Error('The element under the click changed; refusing to click');
    live();
    await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    const hoverHit = await cdp('DOM.getNodeForLocation', {
      x: Math.round(x), y: Math.round(y), includeUserAgentShadowDOM: true,
    });
    if (hoverHit.backendNodeId !== hitBackendId)
      throw new Error('The element under the pointer changed on hover; refusing to click');
    live();
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    live();
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  } else {
    if (target.tag === 'input' || target.tag === 'textarea') replacedLength = Number(target.valueLength) || 0;
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    if (!resolved.object || !resolved.object.objectId) throw new Error('Target node is unavailable');
    live();
    typed = await cdp('Runtime.callFunctionOn', {
      objectId: resolved.object.objectId,
      functionDeclaration: `function(text){
        this.focus();
        const root=this.getRootNode();
        if (root.activeElement !== this) return {ok:false,reason:'focus'};
        const tokens=String(this.autocomplete||'').toLowerCase().split(/\\s+/);
        const replacedLength=(this.tagName.toLowerCase()==='input'||this.tagName.toLowerCase()==='textarea') ? String(this.value||'').length : 0;
        const identity=[this.name,this.id,this.getAttribute('aria-label'),this.placeholder,
          ...(this.labels ? Array.from(this.labels).map(label=>label.innerText||label.textContent||'') : [])].filter(Boolean).join(' ');
        const sensitive=(this.tagName.toLowerCase()==='input' && this.type.toLowerCase()==='password')
          || tokens.some(token=>token.startsWith('cc-') || ['one-time-code','current-password','new-password'].includes(token))
          || /password|senha|passwort|contrase[nñ]a|mot\\s+de\\s+passe|passcode|secret|cvv|cvc|csc|card\\s*number|security\\s+code|c[oó]digo\\s+de\\s+seguran|n[uú]mero\\s+do\\s+cart[aã]o|n[uú]mero\\s+de\\s+tarjeta|kartennummer|\\bpin\\b|token|2fa|otp|c[oó]digo\\s+de\\s+verifica|verification\\s+code/i.test(identity);
        if(sensitive) return {ok:false,reason:'sensitive'};
        const tag=this.tagName.toLowerCase();
        if(tag==='input' || tag==='textarea') {
          const prototype=tag==='input' ? HTMLInputElement.prototype : HTMLTextAreaElement.prototype;
          const setter=Object.getOwnPropertyDescriptor(prototype,'value').set;
          if(!setter) return {ok:false,reason:'setter'};
          setter.call(this,text);
          this.dispatchEvent(new InputEvent('input',{bubbles:true}));
        } else if(this.isContentEditable) {
          if(!this.ownerDocument.execCommand('insertText',false,text)) return {ok:false,reason:'insert'};
        } else return {ok:false,reason:'unsupported'};
        if(this.getRootNode().activeElement !== this) return {ok:false,reason:'focus-after-write'};
        return {ok:true,replacedLength};
      }`,
      arguments: [{ value: text }], returnByValue: true,
    });
    await cdp('Runtime.releaseObject', { objectId: resolved.object.objectId }).catch(() => {});
    if (!typed.result || !typed.result.value || typed.result.value.ok !== true)
      throw new Error('Focus, sensitivity, or text insertion check failed; refusing to type');
  }
  if (operation === 'type' && typed && typed.result && typed.result.value) {
    replacedLength = Number(typed.result.value.replacedLength) || replacedLength;
  }
  return { target, replacedLength, elements: await elements() };
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
    throw new Error(NOT_GRANTED);
  }
  requestedOrigin = undefined;
  await persistState();
}

async function waitForLoad() {
  await cdp('Page.enable');
  await new Promise((resolve, reject) => {
    const done = () => {
      clearTimeout(timeout);
      chrome.debugger.onEvent.removeListener(listener);
      navigationRefusals.delete(refused);
    };
    const timeout = setTimeout(() => { done(); reject(new Error('Page load timed out')); }, 15000);
    const listener = (source, method) => {
      if (source.tabId !== activeTabId || method !== 'Page.loadEventFired') return;
      done();
      resolve();
    };
    const refused = message => { done(); reject(new Error(message)); };
    navigationRefusals.add(refused);
    chrome.debugger.onEvent.addListener(listener);
  });
}

async function handle(message) {
  actionsInFlight += 1;
  try { return await runOperation(message); }
  finally { actionsInFlight -= 1; }
}

async function runOperation(message) {
  if (!message || typeof message.id !== 'number' || typeof message.op !== 'string')
    throw new Error('Invalid request');
  const epoch = stopEpoch;
  const live = () => { if (epoch !== stopEpoch || stopped) throw new Error(STOPPED_MESSAGE); };
  await restoreState();
  if (message.op === 'close') { await stop({ disconnectPort: false }); return true; }
  // Stop holds until the owner's Start: a request that still reaches a live port is refused, not served.
  if (stopped) throw new Error('Chimera Browser was stopped from its popup; click Start in the Chimera extension popup to let Chimera use the browser again');
  // Cancel is the owner's kill-switch, so only the owner's Resume (popup) clears it, never an app request.
  if (actionsDisabled) throw new Error('Chrome debugging was canceled from its bar; click "Resume Chimera actions" in the Chimera extension popup, then request the action again');
  let requested;
  if (message.op === 'navigate') {
    if (typeof message.url !== 'string' || !/^https?:\/\//i.test(message.url)) throw new Error('Only http(s) navigation is supported');
    try { requested = new URL(message.url); } catch { throw new Error('Invalid navigation URL'); }
    await requireGrantedUrl(requested.href);
  }
  live();
  await ensureGroup(requested?.href, live);
  if (!requested) {
    const current = await chrome.tabs.get(activeTabId);
    if (!isWebUrl(current.url)) throw new Error(NOT_A_WEB_TAB);
  }
  live();
  await attach(activeTabId);
  switch (message.op) {
    case 'navigate': {
      await cdp('Page.enable');
      lastRefusal = '';
      live();
      const loading = waitForLoad();
      loading.catch(() => {}); // its outcome is read below; a refusal must not also surface as an unhandled rejection
      const result = await cdp('Page.navigate', { url: message.url });
      if (result.errorText) throw new Error(lastRefusal || 'Navigation failed');
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
      return await actOnTarget('click', message.ref_node, '', message.expected, message.expected_preview, live);
    case 'type':
      if (typeof message.text !== 'string') throw new Error('Invalid text');
      return await actOnTarget('type', message.ref_node, message.text, message.expected, null, live);
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
    case 'challenge_frames': {
      await checkCurrentOrigin();
      const tree = await cdp('Page.getFrameTree');
      const urls = [];
      function collect(frame) {
        if (frame && frame.frame) urls.push(frame.frame.url || '');
        for (const child of (frame && frame.childFrames) || []) collect(child);
      }
      collect(tree.frameTree);
      return urls;
    }
    case 'back': {
      await checkCurrentOrigin();
      const history = await cdp('Page.getNavigationHistory');
      if (history.currentIndex > 0) {
        const entry = history.entries[history.currentIndex - 1];
        // The entry may sit on another origin than the one granted for this page: ask before the request goes out.
        await requireGrantedUrl(entry.url);
        live();
        const loading = waitForLoad();
        await cdp('Page.navigateToHistoryEntry', { entryId: entry.id });
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

    default: throw new Error('Unsupported operation');
  }
}

function pairUsingCode(code) {
  return new Promise((resolve, reject) => {
    const requestId = `pair-${++pairingRequestId}`;
    pairingPending.set(requestId, { resolve, reject });
    try { nativePort?.postMessage({ id: requestId, op: 'pair', code }); }
    catch { pairingPending.delete(requestId); reject(new Error('Pairing unavailable')); }
    setTimeout(() => {
      const pending = pairingPending.get(requestId);
      if (pending) { pairingPending.delete(requestId); reject(new Error('Pairing timed out')); }
    }, 130000);
  });
}

function connectNative() {
  if (nativePort || !mayConnect(stopped)) return;
  nativePort = chrome.runtime.connectNative(HOST);
  nativePort.onMessage.addListener(async message => {
    if (!message) return;
    const pairing = pairingPending.get(message.id);
    if (pairing) {
      pairingPending.delete(message.id);
      if (message.ok) pairing.resolve(message.result);
      else pairing.reject(new Error(message.error || 'Pairing failed'));
      return;
    }
    if (typeof message.op !== 'string') return;
    try {
      const result = await handle(message);
      nativePort?.postMessage({ id: message.id, ok: true, result });
    } catch (error) {
      nativePort?.postMessage({ id: message.id, ok: false, error: String(error) });
    }
  });
  nativePort.onDisconnect.addListener(() => {
    nativePort = undefined;
    for (const pending of pairingPending.values()) pending.reject(new Error('Pairing disconnected'));
    pairingPending.clear();
    setStatus('Disconnected');
  });
  setStatus('Connected');
}

// The popup asks for status on every open. A requested origin the owner has since granted is dropped here,
// so the popup stops offering "Allow" for a site that is already allowed.
async function statusForPopup() {
  await restoreState();
  if (requestedOrigin && await chrome.permissions.contains({ origins: [`${requestedOrigin}/*`] })) {
    requestedOrigin = undefined;
    await persistState();
  }
  const value = await chrome.storage.session.get(['chimeraStatus']);
  // The real state comes from storage: a worker that was recycled has no memory of a Stop, and a stale
  // "Connected" must not be shown while the owner's Stop holds.
  return {
    status: stopped ? 'Stopped' : (value.chimeraStatus || 'Stopped'),
    requestedOrigin: requestedOrigin || '', actionsDisabled, stopped, issues: [...containmentIssues],
  };
}

// Owner action from the popup only (onMessage never receives web pages or the app). Clears the kill-switch.
async function resumeActions() {
  await chrome.storage.local.set({ chimeraActionsDisabled: false });
  actionsDisabled = false;
}

// Owner action from the popup only. The one way out of Stop besides pairing.
async function startFromPopup() {
  await chrome.storage.local.set({ chimeraStopped: false });
  stopped = false;
  connectNative();
}

chrome.runtime.onMessage.addListener((request, _sender, sendResponse) => {
  if (request.op === 'status') {
    statusForPopup().then(sendResponse, () => sendResponse({ status: 'Stopped', requestedOrigin: '', actionsDisabled: false, stopped: true }));
    return true;
  }
  if (request.op === 'resume') {
    resumeActions().then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (request.op === 'start') {
    startFromPopup().then(() => sendResponse({ ok: true })).catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (request.op === 'pair') {
    // Only the popup asks to pair; a pairing request coming from the host side is never honoured.
    if (typeof request.code !== 'string' || !/^\d{6}$/.test(request.code)) { sendResponse({ ok: false }); return false; }
    // Entering a code from Chimera Desktop is the owner's explicit gesture, so it also ends a Stop.
    startFromPopup().then(() => pairUsingCode(request.code))
      .then(result => sendResponse({ ok: result?.ok === true }))
      .catch(() => sendResponse({ ok: false }));
    return true;
  }
  if (request.op === 'stop') {
    stop({ hold: true }).then(issues => sendResponse({ ok: true, issues })).catch(() => sendResponse({ error: 'Could not stop Chimera browser' }));
    return true;
  }
  return false;
});

// Closes one Chimera tab: removed, or at worst sent to about:blank. Resolves to true when the page can no
// longer send anything, so the debugger may be let go of.
async function closeTab(tabId) {
  try { await chrome.tabs.remove(tabId); } catch { /* may already be gone; checked below */ }
  if (!await chrome.tabs.get(tabId).then(() => true, () => false)) return true;
  try {
    // A refused navigation is reported in the RESULT (errorText), not as an exception: it is a failure too.
    const result = await chrome.debugger.sendCommand({ tabId }, 'Page.navigate', { url: 'about:blank' });
    return !(result && result.errorText);
  } catch { return false; }
}

// Order matters (ext, Codex review): letting go of the debugger releases every request still paused, so first
// each paused request is FAILED, then the tabs are closed, and only then is the debugger detached. A failure
// at any step is returned, not hidden; a tab that could not be closed stays attached (its requests stay held).
async function stop({ disconnectPort = true, hold = false } = {}) {
  stopEpoch += 1;
  if (hold) {
    // Written first and to storage.local: even if this worker dies half way, nothing reconnects it.
    await chrome.storage.local.set({ chimeraStopped: true });
    stopped = true;
  }
  await restoreState();
  const issues = [];
  const ownedTabs = groupId === undefined ? [...tabIds] :
    (await chrome.tabs.query({ groupId }).catch(() => [])).map(tab => tab.id);
  for (const [requestId, target] of [...paused]) {
    paused.delete(requestId);
    try { await sendTo(target, 'Fetch.failRequest', { requestId, errorReason: 'BlockedByClient' }); }
    catch (error) { issues.push(`A paused request could not be failed (${error}); its tab is closed instead`); }
  }
  const detachable = [];
  for (const tabId of ownedTabs) {
    if (await closeTab(tabId)) detachable.push(tabId);
    else issues.push(`Tab ${tabId} could not be closed; it stays attached and held`);
  }
  for (const tabId of detachable) {
    try { await chrome.debugger.detach({ tabId }); } catch { /* the tab is closed, so there is nothing attached */ }
  }
  tabIds.clear();
  groupId = undefined;
  activeTabId = undefined;
  requestedOrigin = undefined;
  await persistState();
  if (disconnectPort && nativePort) { nativePort.disconnect(); nativePort = undefined; }
  setStatus('Stopped');
  containmentIssues.push(...issues);
  return issues;
}

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId !== activeTabId) return;
  if (reason === 'canceled_by_user') {
    // Owner pressed Cancel on Chrome's debugging bar: stop everything and stay off until the owner resumes.
    chrome.storage.local.set({ chimeraActionsDisabled: true }).then(() => {
      actionsDisabled = true;
      return stop({ disconnectPort: false });
    }).then(async () => {
      actionsDisabled = true;
      await chrome.storage.local.set({ chimeraActionsDisabled: true });
    });
    return;
  }
  setStatus('Debugger detached');
});

// Every start of this worker, whatever woke it, reads the owner's Stop first; connectNative itself refuses
// while it holds. There is no unconditional connect.
function connectUnlessStopped() { return restoreState().then(connectNative); }
chrome.runtime.onStartup.addListener(connectUnlessStopped);
chrome.runtime.onInstalled.addListener(connectUnlessStopped);
connectUnlessStopped();

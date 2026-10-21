const HOST = 'app.chimera.browser';
const CDP = '1.3';
let nativePort;
let groupId;
let activeTabId;
let nextId = 1;
let requestedOrigin;
let actionsDisabled = false;
const tabIds = new Set();

async function persistState() {
  await chrome.storage.session.set({
    chimeraGroupId: groupId,
    chimeraTabIds: [...tabIds],
    chimeraActiveTabId: activeTabId,
    chimeraRequestedOrigin: requestedOrigin,
    chimeraActionsDisabled: actionsDisabled,
  });
}

async function restoreState() {
  const saved = await chrome.storage.session.get([
    'chimeraGroupId', 'chimeraTabIds', 'chimeraActiveTabId', 'chimeraRequestedOrigin', 'chimeraActionsDisabled',
  ]);
  groupId = saved.chimeraGroupId;
  tabIds.clear();
  for (const tabId of saved.chimeraTabIds || []) tabIds.add(tabId);
  activeTabId = saved.chimeraActiveTabId;
  requestedOrigin = saved.chimeraRequestedOrigin;
  actionsDisabled = saved.chimeraActionsDisabled === true;
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
  await chrome.debugger.sendCommand({ tabId }, 'Page.setDownloadBehavior', { behavior: 'deny' });
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
}

function protectedTarget(target) {
  const autocompleteTokens = String(target.autocomplete || '').toLowerCase().split(/\s+/);
  const identity = [target.name, target.id, target.label, target.ariaLabel, target.placeholder,
    target.accessibleName, target.axName, ...(target.labels || [])].filter(Boolean).join(' ');
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

async function actOnTarget(operation, refNode, text = '', expected = null) {
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
    const freshTarget = await describeTarget(refNode);
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
    await cdp('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y });
    const hoverHit = await cdp('DOM.getNodeForLocation', {
      x: Math.round(x), y: Math.round(y), includeUserAgentShadowDOM: true,
    });
    if (hoverHit.backendNodeId !== hitBackendId)
      throw new Error('The element under the pointer changed on hover; refusing to click');
    await cdp('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await cdp('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  } else {
    if (target.tag === 'input' || target.tag === 'textarea') replacedLength = Number(target.valueLength) || 0;
    const resolved = await cdp('DOM.resolveNode', { backendNodeId: refNode });
    if (!resolved.object || !resolved.object.objectId) throw new Error('Target node is unavailable');
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
  if (message.op === 'close') { await stop({ disconnectPort: false }); return true; }
  if (actionsDisabled) throw new Error('Chrome debugging was canceled; request a new browser action from the app');
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
      return await actOnTarget('click', message.ref_node, '', message.expected);
    case 'type':
      if (typeof message.text !== 'string') throw new Error('Invalid text');
      return await actOnTarget('type', message.ref_node, message.text, message.expected);
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

async function stop({ disconnectPort = true } = {}) {
  await restoreState();
  const ownedTabs = groupId === undefined ? [...tabIds] :
    (await chrome.tabs.query({ groupId }).catch(() => [])).map(tab => tab.id);
  for (const tabId of ownedTabs) {
    try { await chrome.debugger.detach({ tabId }); } catch { /* tab already closed or detached */ }
  }
  if (ownedTabs.length) {
    try { await chrome.tabs.remove(ownedTabs); } catch { /* tabs were already closed */ }
  }
  tabIds.clear();
  groupId = undefined;
  activeTabId = undefined;
  requestedOrigin = undefined;
  await persistState();
  if (disconnectPort && nativePort) { nativePort.disconnect(); nativePort = undefined; }
  setStatus('Stopped');
}

chrome.debugger.onDetach.addListener((source, reason) => {
  if (source.tabId !== activeTabId) return;
  if (reason === 'canceled_by_user') {
    chrome.storage.session.set({ chimeraActionsDisabled: true }).then(() => {
      actionsDisabled = true;
      return stop({ disconnectPort: false });
    }).then(async () => {
      actionsDisabled = true;
      await chrome.storage.session.set({ chimeraActionsDisabled: true });
    });
    return;
  }
  setStatus('Debugger detached');
});

chrome.runtime.onStartup.addListener(connectNative);
chrome.runtime.onInstalled.addListener(connectNative);
restoreState().then(connectNative);
connectNative();

// What Chromium's Fetch.enable accepts as a pattern resourceType. The protocol's Network.ResourceType enum is
// wider (it lists Prefetch, TextTrack, Manifest, ...), but the filter parser in
// content/browser/devtools/protocol/fetch_handler.cc (AddInterceptedResourceType) only knows these, and answers
// anything else with "Unknown resource type in fetch filter". A harness that took any string would pass a
// filter that makes every real attach fail, so the mocks validate against this list.
export const FETCH_FILTER_TYPES = new Set([
  'Document', 'Stylesheet', 'Image', 'Media', 'Font', 'Script', 'XHR', 'Fetch', 'EventSource',
  'CSPViolationReport', 'Ping', 'Other',
]);

export function assertValidFetchFilter(params) {
  for (const pattern of (params && params.patterns) || []) {
    if (pattern.resourceType !== undefined && !FETCH_FILTER_TYPES.has(pattern.resourceType)) {
      throw new Error(`Unknown resource type in fetch filter: '${pattern.resourceType}'`);
    }
  }
}

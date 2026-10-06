export const BASE = '/hermes/';
export class APIError extends Error {
  constructor(message, status = 0, code = '') { super(message); this.status = status; this.code = code; }
}
export function createAPI(fetcher = globalThis.fetch.bind(globalThis)) {
  let csrf = '';
  return {
    clear() { csrf = ''; },
    async request(path, {method = 'GET', body, rawBody, headers:extraHeaders, signal} = {}) {
      if (!path.startsWith('/') || path.startsWith('//') || /[\\\r\n]/.test(path) || path.split(/[/?]/).includes('..')) throw new APIError('Invalid API path.');
      if (body !== undefined && rawBody !== undefined) throw new APIError('Invalid API request body.');
      const headers = {Accept: 'application/json'};
      if (body !== undefined) headers['Content-Type'] = 'application/json';
      Object.assign(headers, extraHeaders || {});
      if (method !== 'GET' && csrf) headers['X-CSRF-Token'] = csrf;
      let response;
      try {
        response = await fetcher(`${BASE}app-api${path}`, {
          method, headers, credentials:'same-origin', cache:'no-store', signal,
          ...(body !== undefined ? {body:JSON.stringify(body)} : {}),
          ...(rawBody !== undefined ? {body:rawBody} : {}),
        });
      } catch (error) {
        if (error.name === 'AbortError') throw error;
        throw new APIError('Cannot connect to Hermes. Check your connection and try again. Nothing was automatically retried.');
      }
      const text = await response.text();
      let data;
      try { data = text ? JSON.parse(text) : {}; }
      catch { throw new APIError(`Hermes returned an unreadable response (${response.status}). Try again when the service is available.`, response.status); }
      if (!response.ok) {
        const detail = data.detail;
        throw new APIError(typeof detail === 'string' ? detail : detail?.message || data.message || `Request failed (${response.status}).`, response.status, detail?.code || data.code);
      }
      if (data.csrf_token) csrf = data.csrf_token;
      return data;
    },
  };
}

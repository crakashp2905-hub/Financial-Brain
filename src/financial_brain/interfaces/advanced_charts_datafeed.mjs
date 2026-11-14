/** Our own read-only Advanced Charts Datafeed API adapter; no TradingView assets. */
const MAX_RANGE_SECONDS = 30 * 86400;
const CHUNK_SECONDS = 7 * 86400;

export function createArchiveDatafeed(baseUrl, fetcher = fetch) {
  const base = new URL(baseUrl);
  const request = async (path, params = {}) => {
    const url = new URL(path, base);
    for (const [key, value] of Object.entries(params)) url.searchParams.set(key, String(value));
    const response = await fetcher(url.toString(), { method: 'GET', credentials: 'same-origin' });
    if (!response.ok) throw new Error(`chart archive HTTP ${response.status}`);
    return response.json();
  };
  const asyncCallback = (fn, value) => queueMicrotask(() => fn(value));
  return Object.freeze({
    onReady(callback) {
      request('config').then(value => asyncCallback(callback, value), () => {
        asyncCallback(callback, { supported_resolutions: ['1'], supports_search: false });
      });
    },
    resolveSymbol(symbolName, onResolved, onError) {
      request('symbols', { symbol: symbolName })
        .then(value => asyncCallback(onResolved, value), error => asyncCallback(onError, error.message));
    },
    searchSymbols(userInput, exchange, symbolType, onResult) {
      request('search', { query: userInput, exchange, type: symbolType, limit: 20 })
        .then(value => asyncCallback(onResult, value), () => asyncCallback(onResult, []));
    },
    getBars(symbolInfo, resolution, periodParams, onResult, onError) {
      const { from, to } = periodParams;
      if (resolution !== '1' || !Number.isInteger(from) || !Number.isInteger(to) ||
          to <= from || to - from > MAX_RANGE_SECONDS) {
        asyncCallback(onError, 'unsupported chart range or resolution');
        return;
      }
      const chunks = [];
      for (let start = from; start < to; start += CHUNK_SECONDS) {
        chunks.push(request('history', { symbol: symbolInfo.ticker, resolution,
                                         from: start, to: Math.min(start + CHUNK_SECONDS, to) }));
      }
      Promise.all(chunks).then(results => {
        const bars = [];
        for (const result of results) {
          if (result.s === 'no_data') continue;
          if (result.s !== 'ok' || !Array.isArray(result.t)) throw new Error('invalid chart archive response');
          const n = result.t.length;
          for (const key of ['o', 'h', 'l', 'c', 'v']) {
            if (!Array.isArray(result[key]) || result[key].length !== n)
              throw new Error('incomplete chart archive response');
          }
          for (let i = 0; i < n; i++) {
            const bar = { time: result.t[i] * 1000, open: result.o[i], high: result.h[i],
                          low: result.l[i], close: result.c[i], volume: result.v[i] };
            if (!Number.isFinite(bar.time) ||
                (bars.length && bar.time <= bars[bars.length - 1].time))
              throw new Error('out-of-order chart archive bars');
            bars.push(bar);
          }
        }
        onResult(bars, { noData: bars.length === 0 });
      }).catch(error => onError(error.message));
    },
    // Archive-only. Do not fabricate live bars or silently poll the broker.
    subscribeBars(_symbolInfo, _resolution, _onTick, _subscriberUID, _onResetCache) {},
    unsubscribeBars(_subscriberUID) {},
  });
}

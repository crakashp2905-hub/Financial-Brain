import assert from 'node:assert/strict';
import test from 'node:test';
import { createArchiveDatafeed } from '../../src/financial_brain/interfaces/advanced_charts_datafeed.mjs';

const feed = (handler) => createArchiveDatafeed('http://localhost:8000/', async url => {
  const data = await handler(new URL(url));
  return { ok: true, json: async () => data };
});

test('resolves a symbol without orders or broker methods', async () => {
  const adapter = feed(url => ({ ticker: url.searchParams.get('symbol'), timezone: 'Asia/Kolkata' }));
  const value = await new Promise((resolve, reject) => adapter.resolveSymbol('KITE:1', resolve, reject));
  assert.equal(value.ticker, 'KITE:1');
  assert.equal('placeOrder' in adapter, false);
  assert.equal('broker' in adapter, false);
});

test('converts archive seconds to chart milliseconds and joins bounded chunks', async () => {
  const adapter = feed(url => {
    const t = Number(url.searchParams.get('from'));
    return { s: 'ok', t: [t], o: [100], h: [101], l: [99], c: [100], v: [10] };
  });
  const bars = await new Promise((resolve, reject) => adapter.getBars(
    { ticker: 'KITE:1' }, '1', { from: 0, to: 8 * 86400 }, resolve, reject));
  assert.equal(bars.length, 2);
  assert.equal(bars[0].time, 0);
  assert.equal(bars[1].time, 7 * 86400 * 1000);
});

test('rejects malformed or unsupported history rather than drawing it', async () => {
  const adapter = feed(() => ({ s: 'ok', t: [1, 2], o: [1], h: [], l: [], c: [], v: [] }));
  const error = await new Promise(resolve => adapter.getBars(
    { ticker: 'KITE:1' }, '1', { from: 0, to: 60 }, () => resolve('unexpected'), resolve));
  assert.match(error, /incomplete/);
  const unsupported = await new Promise(resolve => adapter.getBars(
    { ticker: 'KITE:1' }, '5', { from: 0, to: 60 }, () => resolve('unexpected'), resolve));
  assert.match(unsupported, /unsupported/);
});

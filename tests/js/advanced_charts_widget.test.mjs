import assert from 'node:assert/strict';
import test from 'node:test';
import { mountArchiveChart } from '../../src/financial_brain/interfaces/advanced_charts_widget.mjs';

test('mounts charting widget without broker integration', async () => {
  let options;
  let removed = false;
  const TradingView = { widget: class {
    constructor(value) { options = value; }
    chartReady() { return Promise.resolve(); }
    activeChart() { return { id: 'research-chart' }; }
    changeTheme(value) { return Promise.resolve(value); }
    remove() { removed = true; }
  } };
  const instance = await mountArchiveChart({ TradingView, container: {}, symbol: 'KITE:1',
    libraryPath: '/private-chart-assets/', archiveUrl: 'http://localhost:8000/' });
  assert.equal(options.symbol, 'KITE:1');
  assert.equal(options.timezone, 'Asia/Kolkata');
  assert.equal(options.broker_factory, undefined);
  assert.equal(instance.chart.id, 'research-chart');
  assert.equal(await instance.changeTheme('Light'), 'Light');
  instance.dispose();
  assert.equal(removed, true);
});

test('refuses non-archive symbol', async () => {
  await assert.rejects(mountArchiveChart({ TradingView: { widget: class {} }, container: {},
    symbol: 'NSE:INFY', libraryPath: '/private/', archiveUrl: 'http://localhost/' }));
});

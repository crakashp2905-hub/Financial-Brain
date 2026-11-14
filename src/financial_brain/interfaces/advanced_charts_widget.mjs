/** Host adapter for a separately licensed Advanced Charts asset bundle.
 *  Deliberately uses Charting Library + Datafeed APIs only; never Trading Platform.
 */
import { createArchiveDatafeed } from './advanced_charts_datafeed.mjs';

export async function mountArchiveChart({ TradingView, container, symbol, libraryPath,
                                          archiveUrl, theme = 'Dark', fetcher = fetch }) {
  if (!TradingView?.widget || !container || !/^KITE:[1-9]\d*$/.test(symbol) ||
      !libraryPath || !archiveUrl) throw new Error('invalid chart configuration');
  const widget = new TradingView.widget({
    container,
    library_path: libraryPath,
    datafeed: createArchiveDatafeed(archiveUrl, fetcher),
    symbol,
    interval: '1',
    locale: 'en',
    timezone: 'Asia/Kolkata',
    theme,
    autosize: true,
    // No broker_factory, trading host, order ticket, or execution callback.
  });
  await widget.chartReady();
  return Object.freeze({
    widget,
    chart: widget.activeChart(),
    changeTheme: next => widget.changeTheme(next),
    dispose: () => widget.remove(),
  });
}

# Forward session clock contract

All clocks are timezone-aware UTC instants sourced through one injected
`SessionClockProvider` instance for a capture/session. Exchange time remains the
timestamp supplied by the data source; the provider records local receipt,
availability, decision, ingest, and simulated-execution times.

The required ordering for a market event is:

```text
exchange_utc <= received_utc <= available_utc <= decision_utc
```

The runner only passes events with `available_utc <= decision_utc` to a strategy.
Later events stay in the journal for execution/replay analysis and cannot inform
that decision. A simulated execution receipt must have an aware timestamp at or
after its decision. The provider rejects backwards local clock reads and any
exchange/receive/availability inversion; it never repairs a timestamp by
substituting another clock.

Kite capture takes the receive time after the quote response, the availability
time after normalizing the quote, and the ingest time after building the source
event. Passing explicit legacy timestamps and an injected provider together is
rejected to avoid mixed clock authorities. Tests inject fixed or skewed clock
sequences; production defaults to UTC system time and still needs independent
clock synchronization/attestation before any independent-custody claim.

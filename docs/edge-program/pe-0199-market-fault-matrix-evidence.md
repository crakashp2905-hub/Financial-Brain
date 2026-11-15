# PE-0199 market fault matrix evidence

Ran 133 tests across packet gaps/duplicates/disconnect/reconnect and stale heartbeats; exchange and local clock skew; availability ordering; holidays and special sessions; split/dividend/delisting and invalid corporate-action source; duplicate/expired instruments; deterministic replay mismatch; stale feeds and incomplete session recovery. Faults preserve explicit blocking reasons and do not fabricate fills or close an unresolved session as zero loss.

All test inputs are synthetic and controlled. Live Kite failure modes and actual exchange-session observations remain unmeasured.

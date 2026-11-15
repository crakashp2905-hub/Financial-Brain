# Capture and replay parity (PE-0044)

`verify_capture_replay_equivalence` accepts locally captured and archive-replayed `NormalizedEvent` sequences, requires identical ordered event hashes, and runs both through separate temporary `ForwardSessionRunner` journals with the same frozen configuration and adapters. It hashes each decision together with the visible input event IDs and hashes the durable paper-intent row. The compact result is appended idempotently to a durable parity audit journal.

An input mismatch is logged as non-equivalent without producing signal or intent hashes. Matching inputs must produce identical signal and order-intent hashes for `equivalent=true`. The harness has no broker interface and records `physical_orders=false`.

The harness does not itself obtain or authenticate source data. Callers should provide captured events and `RawQuoteArchive.replay_events()` output from the same rights-gated local archive. Synthetic parity tests establish deterministic offline behavior; live capture, source rights and trading performance remain separate evidence.

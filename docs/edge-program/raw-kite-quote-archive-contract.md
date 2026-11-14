# Raw Kite quote archive contract

`RawQuoteArchive` is an optional, append-only local archive for the canonical JSON serialization of parsed Kite Connect SDK quote objects. It is not a capture of raw HTTP wire bytes. Each record stores the parsed quote, instrument, source, receipt and availability clocks, payload digest, and the durable journal chain identity.

The normalized forward `QUOTE` event carries the archived payload digest and sequence reference. Its content hash is used by the simulated fill receipt, so the lineage is fill receipt → normalized event → archive record. This makes the simulated evidence reproducible when the archive is retained alongside the event journal.

Archiving is opt-in. Construction requires a caller-supplied retention attestation containing an approval flag, authorization identifier, controller, purpose, evidence digest, and timestamp. The code only checks that these fields are present and well-formed; it does not independently verify legal/data-provider rights or authenticate the person supplying them. Do not enable this for real payloads until the operator has verified applicable retention rights and supplied the corresponding evidence.

Synthetic tests verify opt-in rejection, canonical payload linkage, hash-chain tamper detection, fill-to-archive lineage, and forwarding through the existing live poll loop. No authorized live-market capture is included in this repository evidence.

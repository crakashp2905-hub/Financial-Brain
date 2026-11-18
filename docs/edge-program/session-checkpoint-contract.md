# Forward-session journal checkpoints

`forward_edge.session_checkpoints.create_checkpoint` writes one fsynced hash-chain record containing the durable sequence, chain head, and byte-prefix SHA-256 for both the forward event journal and PaperAccount ledger journal. `START_OF_DAY` and `END_OF_DAY` are immutable per `(session_id, exchange_date, phase)`. A retry after a process restart returns and verifies the original checkpoint, even if later events have been appended.

The caller must explicitly attest `writers_quiesced=True` while a new two-journal snapshot is sampled. Each journal is independently validated without opening it through the mutating torn-tail recovery path; the file is checked for stability during sampling. This does not coordinate writers that ignore the quiescence contract. The single checkpoint record itself is appended and fsynced atomically by `DurableJournal`.

`verify_checkpoint_pair` checks identity, phase ordering, time ordering, sequence monotonicity, and that both exact byte prefixes remain present and hash-valid. Later valid journal appends are allowed. A torn or corrupted journal fails verification. These are local integrity controls; the hashes are not externally attested and do not establish market-data rights or accounting correctness against a broker.

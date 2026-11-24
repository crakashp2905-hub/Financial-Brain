# Prompt-injection boundary audit

Date: 2026-11-25

## Implemented controls

- Filing/news classifier prompts delimit external text as untrusted data.
- The shared model router recursively scans text fields in the input row, records matches
  in the `security_findings` table, and refuses the model call when a steering pattern is
  found. This covers headlines, website excerpts, filing text and log excerpts whenever
  they enter through this router contract.
- Ollama, Laya and FinBERT direct classifier adapters enforce the same input policy, so a
  caller bypassing the shared router does not send flagged text to a model.
- The router and individual typed adapters validate output labels, confidence ranges and
  probability distributions against the task's allowed choice set before the output can
  be accepted. Free-form output cannot become an executable decision through these APIs.
- External text exceeding 100,000 characters is rejected; it is not silently truncated.

## Verification

`tests/test_phase2_security.py` and `tests/test_phase2_router.py` exercise ordinary
financial headlines, known instruction-steering patterns, delimiter spoofing, audit-log
records, router refusal before model calls, direct-adapter refusal and typed model-output
validation. The current run and tool results are recorded in the v3 checklist ledger.

## Limitations

Pattern detection is a heuristic and does not prove detection of novel or obfuscated attacks.
The controls apply to these classifier adapters; repository-wide agent tool execution,
web retrieval, log summarization, generated-code execution and every model/output path have
not been independently inventoried and sandbox-verified. Model text remains untrusted
outside the typed choice boundary. This audit is not a claim of complete agent security.

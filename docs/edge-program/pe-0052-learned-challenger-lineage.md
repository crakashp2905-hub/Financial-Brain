# PE-0052 learned challenger lineage and prediction digests

`learned_model.train_final` now records an immutable identity from the exact ordered feature names plus the feature-library source digest, content digest of the labeled training rows, model-training source digest, model artifact digest, parameters, seed, and embargo metadata. The model ID incorporates the training data, schema, code, parameters, seed, and saved model artifact. `load` refuses a model whose artifact no longer matches its manifest or whose manifest predates content-addressed lineage.

`prediction_batch_sha256` hashes the as-of date, model ID, and sorted `(lineage, IEEE-754 score)` records. Daily score output carries the batch hash, model ID, and feature-schema digest. A cached batch is returned only when those values and the current model artifact digest match; legacy or modified cache rows are recalculated.

The current 30-day embargo is a calendar-day proxy. The dataset does not carry exact label-end session timestamps, so this implementation cannot prove that every 20-session label had matured before training. Provider/source rights and prospective challenger evidence are also separate gates. This package adds reproducibility and tamper detection; it does not establish predictive performance.

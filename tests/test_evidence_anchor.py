from datetime import datetime, timezone

import pytest

from financial_brain.evidence.anchor import EvidenceAnchor


def test_anchor_validates_digest_and_aware_past_timestamp():
    anchor = EvidenceAnchor("provider", "a" * 64,
                            datetime.now(timezone.utc), "signed-receipt")
    anchor.validate()


@pytest.mark.parametrize("digest", ["A" * 64, "short", "g" * 64])
def test_anchor_rejects_malformed_digest(digest):
    with pytest.raises(ValueError, match="invalid evidence anchor"):
        EvidenceAnchor("provider", digest, datetime.now(timezone.utc), "receipt").validate()

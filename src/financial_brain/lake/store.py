"""C00 - Immutable raw data lake.

Every byte we receive from any source lands here **before** it is parsed, and is never
mutated afterwards. Each payload is stored next to a sidecar ``.meta.json`` recording
where it came from, when we retrieved it, and its SHA-256.

Why this exists
---------------
Parsed tables are opinions; raw payloads are facts. If a parser is wrong, a source
silently restates history, or a regulator asks how a number was derived, the lake is the
only thing that can answer. It also makes ingestion *replayable*: every curated table can
be rebuilt from the lake without touching the network.

The layout is deliberately plain filesystem so it works with no infrastructure today and
maps 1:1 onto object storage (S3/GCS) later - the key below becomes the object key.

    lake/<source>/<dataset>/<yyyy>/<mm>/<dd>/<filename>
    lake/<source>/<dataset>/<yyyy>/<mm>/<dd>/<filename>.meta.json
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class LakeObject:
    """A single immutable payload plus its provenance."""

    source: str
    dataset: str
    business_date: str          # ISO date the data describes
    filename: str
    url: str
    retrieved_at: str           # ISO-8601 UTC - when *we* saw it
    sha256: str
    size_bytes: int
    content_type: str
    http_status: int

    @property
    def key(self) -> str:
        y, m, d = self.business_date.split("-")
        return f"{self.source}/{self.dataset}/{y}/{m}/{d}/{self.filename}"


class RawLake:
    """Write-once store for source payloads."""

    def __init__(self, root: Path):
        self.root = Path(root)

    # -- paths ---------------------------------------------------------------
    def _path(self, obj_key: str) -> Path:
        return self.root / obj_key

    def _meta_path(self, obj_key: str) -> Path:
        return self.root / (obj_key + ".meta.json")

    # -- write ---------------------------------------------------------------
    def put(
        self,
        *,
        source: str,
        dataset: str,
        business_date: date | str,
        filename: str,
        payload: bytes,
        url: str,
        content_type: str = "application/octet-stream",
        http_status: int = 200,
        retrieved_at: datetime | None = None,
    ) -> LakeObject:
        """Store a payload. Returns the object descriptor.

        If an object already exists at this key with an identical hash the write is a
        no-op - ingestion is idempotent. If the hash *differs*, the existing object is
        preserved and the new one is written beside it with a hash suffix, because a
        source changing a published file is itself a finding worth keeping.
        """
        bdate = business_date.isoformat() if isinstance(business_date, date) else business_date
        digest = hashlib.sha256(payload).hexdigest()
        obj = LakeObject(
            source=source,
            dataset=dataset,
            business_date=bdate,
            filename=filename,
            url=url,
            retrieved_at=(retrieved_at or datetime.now(timezone.utc)).isoformat(),
            sha256=digest,
            size_bytes=len(payload),
            content_type=content_type,
            http_status=http_status,
        )

        path = self._path(obj.key)
        if path.exists():
            existing = json.loads(self._meta_path(obj.key).read_text(encoding="utf-8"))
            if existing.get("sha256") == digest:
                return LakeObject(**existing)          # already have exactly this
            # Source republished different bytes for the same key - keep both.
            obj = LakeObject(**{**asdict(obj), "filename": f"{filename}.{digest[:12]}"})
            path = self._path(obj.key)

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        self._meta_path(obj.key).write_text(
            json.dumps(asdict(obj), indent=2, sort_keys=True), encoding="utf-8"
        )
        return obj

    # -- read ----------------------------------------------------------------
    def read(self, obj: LakeObject | str) -> bytes:
        key = obj if isinstance(obj, str) else obj.key
        data = self._path(key).read_bytes()
        meta = self.meta(key)
        if meta and hashlib.sha256(data).hexdigest() != meta.sha256:
            raise IOError(f"lake corruption: hash mismatch for {key}")
        return data

    def meta(self, obj_key: str) -> LakeObject | None:
        p = self._meta_path(obj_key)
        if not p.exists():
            return None
        return LakeObject(**json.loads(p.read_text(encoding="utf-8")))

    def exists(self, source: str, dataset: str, business_date: date | str, filename: str) -> bool:
        bdate = business_date.isoformat() if isinstance(business_date, date) else business_date
        y, m, d = bdate.split("-")
        return self._path(f"{source}/{dataset}/{y}/{m}/{d}/{filename}").exists()

    def iter_objects(self, source: str | None = None, dataset: str | None = None):
        """Yield every LakeObject, optionally filtered."""
        base = self.root
        if source:
            base = base / source
            if dataset:
                base = base / dataset
        if not base.exists():
            return
        for meta_file in sorted(base.rglob("*.meta.json")):
            yield LakeObject(**json.loads(meta_file.read_text(encoding="utf-8")))

# SPDX-License-Identifier: MIT

"""Content-addressed provenance for normalized raw-source records."""

from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path


def source_provenance_fields(path: Path) -> dict[str, str]:
    """Return basename-preserving, content-addressed source provenance fields.

    ``source_id`` deliberately identifies the immutable source content.  This
    keeps normalized records stable when the same raw corpus is relocated,
    while ``source_file`` remains the required human-readable basename.
    """

    digest = source_sha256(path)
    return {
        "source_file": path.name,
        "source_id": f"sha256:{digest}",
        "source_sha256": digest,
    }


@lru_cache(maxsize=None)
def source_sha256(path: Path) -> str:
    """Return the SHA-256 digest of one authoritative raw source file."""

    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()

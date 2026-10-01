"""Collect findings for one file: one row per unique value per nested part.

Raw values are reduced to an in-memory digest for de-duplication; neither the
value nor the digest is ever returned or stored (a digest of a 12-digit ID could
be brute-forced).
"""
import hashlib
from collections import defaultdict

from .patterns import CREDENTIAL_TYPES


class Aggregator:
    def __init__(self, max_rows: int = 100_000, max_distinct: int = 1_000_000):
        self.max_rows, self.max_distinct = max_rows, max_distinct
        self.rows = {}
        self.distinct = defaultdict(set)
        self.truncated = False

    def add(self, inner_path: str, hit, location: str):
        digest = hashlib.blake2b(hit.value.encode("utf-8", "replace"), digest_size=12).digest()
        key = (inner_path, hit.type, digest)
        row = self.rows.get(key)
        if row:
            row["occurrences"] += 1
            if hit.confidence == "high":
                row["confidence"] = "high"
        elif len(self.rows) < self.max_rows:
            self.rows[key] = {"inner_path": inner_path, "data_type": hit.type, "masked_value": hit.masked,
                              "confidence": hit.confidence, "occurrences": 1, "first_location": location}
        else:
            self.truncated = True
        seen = self.distinct[hit.type]
        if len(seen) < self.max_distinct:
            seen.add(digest)

    def counts(self) -> dict:
        """Distinct values per type across the whole file."""
        return {t: len(s) for t, s in self.distinct.items() if s}

    def findings(self) -> list:
        return list(self.rows.values())

    def has_credentials(self) -> bool:
        return any(t in CREDENTIAL_TYPES for t in self.distinct)

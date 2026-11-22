"""Bounded scan safety: full coverage or failure, never silent truncation."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from governedgraph.learning.gap_review_job import (
    GapReviewCapacityError, MAX_RESPONSE_BLOB_BYTES, read_records,
)


class Container:
    def __init__(self, payload: bytes):
        self.payload = payload

    def list_blobs(self, name_starts_with=""):
        return [SimpleNamespace(name="response.jsonl",
                                last_modified=datetime(2026, 11, 22, tzinfo=timezone.utc),
                                size=len(self.payload))]

    def get_blob_client(self, name):
        assert name == "response.jsonl"
        return self

    def download_blob(self):
        return self

    def readall(self):
        return self.payload


def test_empty_capacity_must_not_succeed():
    with pytest.raises(ValueError, match="positive"):
        read_records(Container(b""), datetime(2026, 11, 1, tzinfo=timezone.utc), max_records=0)


def test_oversized_record_aborts_before_parsing():
    with pytest.raises(GapReviewCapacityError, match="oversized"):
        read_records(Container(b"x" * (MAX_RESPONSE_BLOB_BYTES + 1)),
                     datetime(2026, 11, 1, tzinfo=timezone.utc))


def test_response_byte_budget_fails_closed():
    with pytest.raises(GapReviewCapacityError, match="scan budget"):
        read_records(Container(b'{"a":1}\n'),
                     datetime(2026, 11, 1, tzinfo=timezone.utc), max_total_bytes=1)

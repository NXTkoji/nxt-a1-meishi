"""Unit tests for merge_persons endpoint signature and logic."""
import inspect
import pytest


def test_merge_endpoint_exists():
    from app.routers.v2.persons import merge_persons
    sig = inspect.signature(merge_persons)
    assert 'primary_ext_id' in sig.parameters
    assert 'body' in sig.parameters


def test_merge_request_schema():
    from app.schemas.api import MergeRequest
    req = MergeRequest(source_ids=["abc", "def"])
    assert req.source_ids == ["abc", "def"]


def test_merge_result_schema():
    from app.schemas.api import MergeResult
    import inspect
    sig = inspect.signature(MergeResult)
    assert 'duplicate_contact_count' in sig.parameters

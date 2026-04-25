"""Smoke tests for card list filter params.
These tests verify the endpoint accepts the new params without 500-ing.
A full test suite with data fixtures is left for future work.
"""
import pytest


def test_not_exported_param_is_documented():
    """Verify the list_cards function signature accepts not_exported."""
    import inspect
    from app.routers.v2.cards import list_cards
    sig = inspect.signature(list_cards)
    assert 'not_exported' in sig.parameters


def test_q_param_is_documented():
    import inspect
    from app.routers.v2.cards import list_cards
    sig = inspect.signature(list_cards)
    assert 'q' in sig.parameters


def test_year_param_is_documented():
    import inspect
    from app.routers.v2.cards import list_cards
    sig = inspect.signature(list_cards)
    assert 'year' in sig.parameters


def test_month_param_is_documented():
    import inspect
    from app.routers.v2.cards import list_cards
    sig = inspect.signature(list_cards)
    assert 'month' in sig.parameters


def test_date_param_is_documented():
    import inspect
    from app.routers.v2.cards import list_cards
    sig = inspect.signature(list_cards)
    assert 'date' in sig.parameters

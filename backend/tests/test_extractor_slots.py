"""Deterministic guards for the extractor-slot fixes found during bug-hunting:
- a relationship may carry a `count` attribute ("two kids" -> count=2), so a
  number is never stored as the value;
- a non-food favourite (favourite_movie / favourite_book) is a valid preference
  key and survives cleaning, instead of being forced into food_likes."""
from __future__ import annotations

from app import extraction, ontology


def _clean(**a):
    a.setdefault("subject", "self")
    a.setdefault("operation", "ADD")
    a.setdefault("confidence", 0.9)
    return extraction._clean(a)


def test_relationship_count_attribute_is_allowed():
    assert ontology.allowed_attribute("relationship", "count") is True
    cleaned, reason = _clean(
        subject="relationship/child=children", dimension="relationship", key="child",
        value="children", quote="i have two kids",
        attributes=[{"name": "count", "value": "2"}])
    assert reason is None and cleaned is not None
    assert cleaned["attributes"] == [{"name": "count", "value": "2", "unit": None}]


def test_non_food_favourite_key_is_kept():
    for key, val in [("favourite_movie", "interstellar"), ("favourite_book", "dune")]:
        cleaned, reason = _clean(dimension="preference", key=key, value=val,
                                 quote=f"my favourite is {val}")
        assert reason is None and cleaned is not None, (key, reason)
        assert cleaned["dimension"] == "preference" and cleaned["key"] == key


def test_food_likes_still_valid_for_food():
    cleaned, reason = _clean(dimension="preference", key="food_likes", value="momo",
                             quote="i love momo")
    assert reason is None and cleaned is not None and cleaned["key"] == "food_likes"

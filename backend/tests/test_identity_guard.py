"""The identity self-reference guard must be scoped to identity/name.

Regression: a multi-fact intro ("i'm kiran / i'm studying CS / i live in a
hostel") lost its location + occupation because the strict name-style guard was
applied to every identity key. Name stays strict (never write the user's name
from "her name is X"); other identity keys only need a first-person reference."""
from __future__ import annotations

from app import extraction


def _clean(**a):
    a.setdefault("subject", "self")
    a.setdefault("operation", "ADD")
    a.setdefault("confidence", 0.9)
    return extraction._clean(a)


def test_self_location_is_kept():
    cleaned, reason = _clean(dimension="identity", key="location", value="hostel",
                             quote="i live in a hostel near campus")
    assert reason is None and cleaned is not None
    assert cleaned["dimension"] == "identity" and cleaned["key"] == "location"


def test_self_occupation_is_kept():
    cleaned, reason = _clean(dimension="identity", key="occupation", value="student",
                             quote="i'm studying computer science in my third year")
    assert reason is None and cleaned is not None
    assert cleaned["key"] == "occupation"


def test_name_from_third_person_is_rejected():
    # The original bug this guard solved: "her name is X" must not name the user.
    cleaned, reason = _clean(dimension="identity", key="name", value="hailey",
                             quote="her name is hailey")
    assert cleaned is None and reason == "subject_mismatch"


def test_name_from_self_is_kept():
    cleaned, reason = _clean(dimension="identity", key="name", value="kiran", quote="i'm kiran")
    assert reason is None and cleaned is not None and cleaned["key"] == "name"


def test_third_person_location_is_rejected():
    # No first-person reference -> not the user's identity fact.
    cleaned, reason = _clean(dimension="identity", key="location", value="paris",
                             quote="the office is in paris")
    assert cleaned is None and reason == "subject_mismatch"

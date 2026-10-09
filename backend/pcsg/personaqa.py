"""Persona QA generator (STEP 5): 8-10 questions per persona from its truth
table — current values, a changed value, an ended fact, entity facts, and 2
never-mentioned facts whose correct answer is "don't know".

Each question carries a `gold` (expected answer text) or the sentinel
"__unknown__" for never-mentioned facts; `kind` buckets it for metrics.
"""
from __future__ import annotations

from .datagen import Persona


def make_qa(p: Persona) -> list[dict]:
    f = p.facts
    qa: list[dict] = []

    def add(kind, question, gold):
        qa.append({"persona": p.pid, "kind": kind, "question": question, "gold": gold})

    # current values
    add("current", "what's my name?", f["name"].value)
    add("current", "what do i do for work?", f["job"].value)            # post-change job
    add("current", "how old am i?", f["age"].value)
    add("current", "name a food i like.", f["food"].value)
    add("current", f"am i good at {f['skill2'].value}?", f["skill2"].value)
    # changed value (current city, not the old one)
    add("changed", "where do i live now?", f["city"].value)
    # ended fact asked in present tense
    add("ended", f"do i still {f['hobby'].value}?", f"no longer {f['hobby'].value}")
    # entity facts
    add("entity", f"what's my {f['pet'].key}'s name?", f["pet"].attrs.get("name", ""))
    add("entity", f"what does my {f['sib'].key} do?", f["sib"].attrs.get("occupation", ""))
    # never-mentioned -> correct answer is "don't know"
    add("unknown", "what's my favourite movie?", "__unknown__")
    add("unknown", "what car do i drive?", "__unknown__")
    return qa

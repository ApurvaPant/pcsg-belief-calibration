"""Persona generator — reusable experiment tooling (S9 + the experiments).

Truth tables are defined in CODE and assembled deterministically from a `seed`,
so a run is reproducible and the expected final graph is known exactly. Each
persona carries:
  - stable facts (name, city, job, language, food, hobby)
  - 2 CHANGES over time (city move, job switch) -> new value supersedes old
  - 1 END (a hobby stopped) -> not active at the end
  - 2 ENTITY facts (a pet with a name, a sibling with an occupation)
  - 1 HEDGED fact (low commitment)
  - NOISE turns (a question, a passing mood) that must produce no node

`verbalise()` renders each turn as a user message with a per-turn style
(full / fragment / typo / when_asked), so the pipeline is exercised on messy,
realistic input. The experiments import this module with a DIFFERENT seed;
seed=1000 is reserved for testing only.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

# Pools large enough for 20+ distinct personas.
NAMES = ["kiran", "riya", "arjun", "meera", "dev", "sana", "rohan", "isha", "veer", "tara",
         "nikhil", "priya", "aman", "neha", "karan", "diya", "yash", "anika", "raj", "zoya",
         "omar", "lila", "sam", "jaya"]
CITIES = ["pune", "bangalore", "chennai", "delhi", "mumbai", "kolkata", "jaipur", "kochi",
          "hyderabad", "lucknow", "indore", "nagpur", "surat", "bhopal"]
JOBS = ["data analyst", "teacher", "nurse", "architect", "chef", "accountant", "designer",
        "electrician", "journalist", "pharmacist", "lawyer", "photographer"]
LANGS = ["french", "spanish", "german", "japanese", "tamil", "bengali", "arabic", "dutch"]
FOODS = ["ramen", "biryani", "dosa", "sushi", "pasta", "tacos", "paneer", "noodles"]
HOBBIES = ["jogging", "painting", "cycling", "gardening", "swimming", "baking", "chess", "hiking"]
PETS = [("dog", "bruno"), ("cat", "mochi"), ("dog", "rex"), ("cat", "luna"), ("dog", "max"), ("rabbit", "coco")]
SIBS = [("sister", "doctor"), ("brother", "chef"), ("sister", "lawyer"), ("brother", "pilot")]
HEDGES = [("interest", "topic", "astronomy"), ("interest", "topic", "philosophy"),
          ("belief", "minimalism", "minimalism"), ("interest", "topic", "crypto")]


@dataclass
class Fact:
    dimension: str
    key: str
    value: str
    kind: str                   # stable | change | end | entity | hedged
    attrs: dict[str, str] = field(default_factory=dict)
    superseded_by: str | None = None   # for a 'change' pre-value: the new value


@dataclass
class Turn:
    text: str
    reveals: str | None          # fact id it expresses, or None for noise
    style: str                   # full | fragment | typo | when_asked | noise
    about: str | None = None     # keyword the assistant must mention to trigger when_asked


@dataclass
class Persona:
    pid: int
    name: str
    facts: dict[str, Fact]       # id -> Fact (final truth table)
    turns: list[Turn]

    def expected_active(self) -> list[Fact]:
        """Facts that must be ACTIVE at the final time step (post-change, post-END)."""
        return [f for f in self.facts.values() if f.kind != "end"
                and f.superseded_by is None]

    def ended_fact(self) -> Fact | None:
        return next((f for f in self.facts.values() if f.kind == "end"), None)


def _typo(s: str) -> str:
    """Drop one interior character to simulate a realistic typo."""
    if len(s) < 5:
        return s
    i = len(s) // 2
    return s[:i] + s[i + 1:]


def _verb(style: str, full: str, fragment: str) -> str:
    if style == "fragment":
        return fragment
    if style == "typo":
        return _typo(full)
    return full


def generate_personas(seed: int, n: int = 20) -> list[Persona]:
    out: list[Persona] = []
    for i in range(n):
        rng = random.Random(seed * 100003 + i)
        name = NAMES[rng.randrange(len(NAMES))]
        city1, city2 = rng.sample(CITIES, 2)
        job1, job2 = rng.sample(JOBS, 2)
        lang = rng.choice(LANGS)
        food = rng.choice(FOODS)
        hobby_end = rng.choice(HOBBIES)
        pet_kind, pet_name = rng.choice(PETS)
        sib_rel, sib_occ = rng.choice(SIBS)
        hdim, hkey, hval = rng.choice(HEDGES)

        facts = {
            "name": Fact("identity", "name", name, "stable"),
            "city": Fact("identity", "location", city2, "change"),      # final city
            "job": Fact("identity", "occupation", job2, "change"),      # final job
            "lang": Fact("skill", lang, lang, "stable"),
            "food": Fact("preference", "food_likes", food, "stable"),
            "pet": Fact("relationship", "pet", pet_kind, "entity", {"name": pet_name}),
            "sib": Fact("relationship", sib_rel, sib_rel, "entity", {"occupation": sib_occ}),
            "hedge": Fact(hdim, hkey, hval, "hedged"),
            # pre-change values (superseded at the end) + the ended hobby
            "city_old": Fact("identity", "location", city1, "change", superseded_by=city2),
            "job_old": Fact("identity", "occupation", job1, "change", superseded_by=job2),
            "hobby": Fact("habit", hobby_end, hobby_end, "end"),
        }

        styles = ["full", "full", "full", "typo", "full", "full"]
        turns = [
            Turn(f"hey, i'm {name}", "name", "full"),
            Turn(f"i live in {city1}", "city_old", "full"),
            Turn(f"i work as a {job1}", "job_old", "full"),
            Turn(_verb("typo", f"i speak {lang}", lang), "lang", "typo"),
            Turn(f"i love {food}", "food", "full"),
            Turn(f"i started {hobby_end}", "hobby", "full"),
            Turn(f"my {pet_kind}'s name is {pet_name}", "pet", "full"),
            Turn(f"my {sib_rel} is a {sib_occ}", "sib", "full"),
            Turn(f"i think i'm kind of into {hval}", "hedge", "full"),
            Turn("what can you help me with?", None, "noise"),
            Turn(f"actually i moved to {city2}", "city", "full"),
            Turn(f"i switched jobs, now i'm a {job2}", "job", "full"),
            Turn(f"i stopped {hobby_end}", "hobby_end_event", "full"),
            Turn("ugh i'm so tired today", None, "noise"),
            Turn(f"my {sib_rel} visited, by the way", None, "when_asked", about=sib_rel),
        ]
        out.append(Persona(i, name, facts, turns))
    return out


def generator_self_check(p: Persona) -> dict[str, Any]:
    """What the persona's own turns actually express (to measure generator error:
    facts whose value never appears in any user turn)."""
    said = " ".join(t.text.lower() for t in p.turns)
    expressed, missing = [], []
    for fid, f in p.facts.items():
        if f.kind == "end" or f.superseded_by:
            continue
        tok = f.value.split()[0]
        (expressed if tok in said else missing).append(fid)
    return {"expressed": expressed, "not_expressed": missing}

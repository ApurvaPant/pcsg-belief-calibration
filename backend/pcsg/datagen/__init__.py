"""Persona generator — reusable experiment tooling (S9 + the experiments).

Truth tables are defined in CODE and assembled deterministically from a `seed`.
Each persona (~25 turns) carries stable facts, 2 CHANGES (city, job), 1 END
(a stopped hobby), 2 ENTITY facts (pet+name, sibling+occupation), 1 HEDGED
fact, and NOISE turns. `turn_expected()` yields the assertion(s) the oracle
should produce for a turn; `split_personas()` splits 40/40/20 BY PERSONA.

seed=1000 is reserved for TESTING; experiments use seed=2026.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any

NAMES = ["kiran", "riya", "arjun", "meera", "dev", "sana", "rohan", "isha", "veer", "tara",
         "nikhil", "priya", "aman", "neha", "karan", "diya", "yash", "anika", "raj", "zoya",
         "omar", "lila", "sam", "jaya", "ира", "noah", "amara", "ken", "bea", "ravi"]
CITIES = ["pune", "bangalore", "chennai", "delhi", "mumbai", "kolkata", "jaipur", "kochi",
          "hyderabad", "lucknow", "indore", "nagpur", "surat", "bhopal", "goa", "patna"]
JOBS = ["data analyst", "teacher", "nurse", "architect", "chef", "accountant", "designer",
        "electrician", "journalist", "pharmacist", "lawyer", "photographer", "dentist", "pilot"]
LANGS = ["french", "spanish", "german", "japanese", "tamil", "bengali", "arabic", "dutch", "korean"]
FOODS = ["ramen", "biryani", "dosa", "sushi", "pasta", "tacos", "paneer", "noodles", "momos", "pho"]
HOBBIES = ["jogging", "painting", "cycling", "gardening", "swimming", "baking", "chess", "hiking",
           "sketching", "pottery", "climbing", "surfing"]
SKILLS = ["python", "excel", "guitar", "welding", "photoshop", "sql", "driving", "chess"]
BELIEFS = [("belief", "climate_action", "climate action matters"),
           ("belief", "lifelong_learning", "lifelong learning"),
           ("belief", "work_life_balance", "work-life balance")]
PROJECTS = ["mobile app", "garden shed", "novel", "startup", "podcast", "home reno"]
GOALS = [("goal", "fitness", "run a half marathon"), ("goal", "career", "become a manager"),
         ("goal", "financial", "save for a house"), ("goal", "learning", "learn to code")]
PETS = [("dog", "bruno"), ("cat", "mochi"), ("dog", "rex"), ("cat", "luna"), ("dog", "max"), ("rabbit", "coco")]
SIBS = [("sister", "doctor"), ("brother", "chef"), ("sister", "lawyer"), ("brother", "pilot")]
HEDGES = [("interest", "topic", "astronomy"), ("interest", "topic", "philosophy"),
          ("interest", "topic", "crypto"), ("interest", "topic", "jazz")]


@dataclass
class Fact:
    dimension: str
    key: str
    value: str
    kind: str                    # stable | change | end | entity | hedged
    attrs: dict[str, str] = field(default_factory=dict)
    superseded_by: str | None = None


@dataclass
class Turn:
    text: str
    reveals: str | None
    style: str                    # full | fragment | typo | when_asked | noise | end_event
    about: str | None = None


@dataclass
class Persona:
    pid: int
    name: str
    facts: dict[str, Fact]
    turns: list[Turn]
    split: str = "fit"

    def expected_active(self) -> list[Fact]:
        return [f for f in self.facts.values() if f.kind != "end" and f.superseded_by is None]

    def ended_fact(self) -> Fact | None:
        return next((f for f in self.facts.values() if f.kind == "end"), None)


def _typo(s: str) -> str:
    return s if len(s) < 5 else s[: len(s) // 2] + s[len(s) // 2 + 1:]


def generate_personas(seed: int, n: int = 100) -> list[Persona]:
    out: list[Persona] = []
    for i in range(n):
        rng = random.Random(seed * 100003 + i)
        name = NAMES[rng.randrange(len(NAMES))]
        city1, city2 = rng.sample(CITIES, 2)
        job1, job2 = rng.sample(JOBS, 2)
        lang = rng.choice(LANGS)
        food, food2 = rng.sample(FOODS, 2)
        hobby_end = rng.choice(HOBBIES)
        hobby2 = rng.choice([h for h in HOBBIES if h != hobby_end])
        skill = rng.choice(SKILLS)
        bdim, bkey, bval = rng.choice(BELIEFS)
        project = rng.choice(PROJECTS)
        gdim, gkey, gval = rng.choice(GOALS)
        pet_kind, pet_name = rng.choice(PETS)
        sib_rel, sib_occ = rng.choice(SIBS)
        hdim, hkey, hval = rng.choice(HEDGES)
        age = str(rng.randint(19, 54))

        facts = {
            "name": Fact("identity", "name", name, "stable"),
            "city": Fact("identity", "location", city2, "change"),
            "job": Fact("identity", "occupation", job2, "change"),
            "age": Fact("identity", "age_range", age, "stable"),
            "lang": Fact("skill", lang, lang, "stable"),
            "skill2": Fact("skill", skill, skill, "stable"),
            "food": Fact("preference", "food_likes", food, "stable"),
            "food2": Fact("preference", "food_likes", food2, "stable"),
            "hobby2": Fact("habit", hobby2, hobby2, "stable"),
            "belief": Fact(bdim, bkey, bval, "stable"),
            "project": Fact("project", project.replace(" ", "_"), project, "stable"),
            "goal": Fact(gdim, gkey, gval, "stable"),
            "pet": Fact("relationship", "pet", pet_kind, "entity", {"name": pet_name}),
            "sib": Fact("relationship", sib_rel, sib_rel, "entity", {"occupation": sib_occ}),
            "hedge": Fact(hdim, hkey, hval, "hedged"),
            "city_old": Fact("identity", "location", city1, "change", superseded_by=city2),
            "job_old": Fact("identity", "occupation", job1, "change", superseded_by=job2),
            "hobby": Fact("habit", hobby_end, hobby_end, "end"),
        }
        turns = [
            Turn(f"hey, i'm {name}", "name", "full"),
            Turn(f"i live in {city1}", "city_old", "full"),
            Turn(f"i work as a {job1}", "job_old", "full"),
            Turn(f"i'm {age}", "age", "full"),
            Turn(_typo(f"i speak {lang}"), "lang", "typo"),
            Turn(f"i love {food}", "food", "full"),
            Turn(f"i also really like {food2}", "food2", "full"),
            Turn(f"i started {hobby_end}", "hobby", "full"),
            Turn(f"i do {hobby2} regularly", "hobby2", "full"),
            Turn(f"i'm good at {skill}", "skill2", "full"),
            Turn("what can you help me with?", None, "noise"),
            Turn(f"my {pet_kind}'s name is {pet_name}", "pet", "full"),
            Turn(f"my {sib_rel} is a {sib_occ}", "sib", "full"),
            Turn(f"i believe {bval}", "belief", "full"),
            Turn(f"i'm working on a {project}", "project", "full"),
            Turn(f"i want to {gval}", "goal", "full"),
            Turn(f"i think i'm kind of into {hval}", "hedge", "full"),
            Turn("ugh i'm so tired today", None, "noise"),
            Turn(f"actually i moved to {city2}", "city", "full"),
            Turn(f"i switched jobs, now i'm a {job2}", "job", "full"),
            Turn(f"i stopped {hobby_end}", "hobby_end_event", "end_event"),
            Turn("anyway, how are you?", None, "noise"),
            Turn(f"my {sib_rel} visited yesterday", None, "when_asked", about=sib_rel),
            Turn("what do you know about me?", None, "noise"),
            Turn("cool, thanks!", None, "noise"),
        ]
        out.append(Persona(i, name, facts, turns))
    return split_personas(out)


def split_personas(personas: list[Persona]) -> list[Persona]:
    """Deterministic 40% fit / 40% test / 20% dev BY PERSONA (by index)."""
    n = len(personas)
    for p in personas:
        frac = p.pid / max(1, n)
        p.split = "fit" if frac < 0.40 else ("test" if frac < 0.80 else "dev")
    return personas


def assertion_for(f: Fact, operation: str | None = None) -> dict:
    """Convert a Fact to the oracle's expected-assertion dict."""
    is_entity = f.kind == "entity"
    subj = f"relationship/{f.key}={f.value}" if is_entity else "self"
    return {"dimension": f.dimension, "key": f.key, "value": f.value,
            "operation": operation or ("ADD"), "subject": subj,
            "explicitness": "explicit", "attributes": [{"name": k, "value": v} for k, v in f.attrs.items()]}


def turn_expected(persona: Persona, turn: Turn) -> list[dict]:
    """The assertion(s) a turn is meant to produce (for oracle_mock + labelling)."""
    if turn.reveals is None:
        return []
    if turn.reveals == "hobby_end_event":
        return [assertion_for(persona.facts["hobby"], operation="END")]
    f = persona.facts.get(turn.reveals)
    return [assertion_for(f)] if f else []


def generator_self_check(p: Persona) -> dict[str, Any]:
    said = " ".join(t.text.lower() for t in p.turns)
    expressed, missing = [], []
    for fid, f in p.facts.items():
        if f.kind == "end" or f.superseded_by:
            continue
        if f.value.split()[0] in said:
            expressed.append(fid)
        else:
            missing.append(fid)
    return {"expressed": expressed, "not_expressed": missing}

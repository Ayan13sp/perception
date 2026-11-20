"""Golden dataset schema, loading, validation and stratified sampling."""
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field, model_validator

DATASET_DIR = Path(__file__).parent / "datasets"
DEFAULT_DATASET = DATASET_DIR / "golden_v1.json"


class Criterion(BaseModel):
    id: str
    description: str
    points: float = Field(gt=0)


class Note(BaseModel):
    source: str
    content: str


class Question(BaseModel):
    id: str
    subject: str
    question: str
    model_answer: str
    rubric: list[Criterion] = Field(min_length=1)
    reference_notes: list[Note] = []

    @model_validator(mode="after")
    def rubric_totals_ten(self):
        total = sum(c.points for c in self.rubric)
        if abs(total - 10) > 1e-6:
            raise ValueError(f"{self.id}: rubric points total {total}, expected 10")
        return self


class Item(BaseModel):
    id: str
    question_id: str
    category: str
    answer: str = Field(min_length=1)
    reference_score: int = Field(ge=0, le=10)
    acceptable_range: tuple[int, int]
    attack: Optional[str] = None
    notes: Optional[str] = None

    @property
    def adversarial(self) -> bool:
        return self.category == "adversarial"

    @model_validator(mode="after")
    def consistent(self):
        lo, hi = self.acceptable_range
        if not (0 <= lo <= self.reference_score <= hi <= 10):
            raise ValueError(f"{self.id}: reference {self.reference_score} outside range {self.acceptable_range}")
        if self.adversarial != (self.attack is not None):
            raise ValueError(f"{self.id}: adversarial items (and only those) need an attack type")
        return self


class Dataset(BaseModel):
    name: str
    version: str
    description: str
    label_policy: str
    questions: list[Question]
    items: list[Item]

    @model_validator(mode="after")
    def references_resolve(self):
        qids = {q.id for q in self.questions}
        ids = [i.id for i in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate item ids")
        missing = {i.question_id for i in self.items} - qids
        if missing:
            raise ValueError(f"items reference unknown questions: {missing}")
        return self

    def question(self, qid: str) -> Question:
        return next(q for q in self.questions if q.id == qid)

    @property
    def tag(self) -> str:
        return f"{self.name}@{self.version}"


def load_dataset(path: Path | str = DEFAULT_DATASET) -> Dataset:
    return Dataset.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))


def spread_order(items: list[Item], seed: int = 7) -> list[Item]:
    """
    Reorder items so every prefix keeps roughly the full benign/adversarial and
    category mix. A run stopped early by a provider quota stays representative.
    """
    rng = random.Random(seed)
    adversarial = [i for i in items if i.adversarial]
    rng.shuffle(adversarial)
    by_category: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        if not item.adversarial:
            by_category[item.category].append(item)
    for group in by_category.values():
        rng.shuffle(group)
    # benign items: round-robin across categories
    benign: list[Item] = []
    while any(by_category.values()):
        for key in sorted(by_category):
            if by_category[key]:
                benign.append(by_category[key].pop())
    # interleave so the adversarial share of every prefix tracks the overall share
    share = len(adversarial) / len(items) if items else 0
    ordered: list[Item] = []
    a = b = 0
    while len(ordered) < len(items):
        want_adv = round((len(ordered) + 1) * share)
        if a < len(adversarial) and (a < want_adv or b >= len(benign)):
            ordered.append(adversarial[a])
            a += 1
        else:
            ordered.append(benign[b])
            b += 1
    return ordered


def stratified_sample(items: list[Item], n: int, seed: int = 7) -> list[Item]:
    """A reproducible subset that keeps the benign/adversarial and category mix."""
    if n >= len(items):
        return list(items)
    rng = random.Random(seed)
    adversarial = [i for i in items if i.adversarial]
    rng.shuffle(adversarial)
    # adversarial items get their proportional share of the sample (at least one)
    adv_quota = min(len(adversarial), max(1, round(n * len(adversarial) / len(items))))
    picked: list[Item] = adversarial[:adv_quota]

    groups: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        if not item.adversarial:
            groups[item.category].append(item)
    for group in groups.values():
        rng.shuffle(group)
    # round-robin across benign categories so each is represented before any repeats
    keys = sorted(groups)
    while len(picked) < n and any(groups.values()):
        for key in keys:
            if groups[key] and len(picked) < n:
                picked.append(groups[key].pop())
    order = {item.id: i for i, item in enumerate(items)}
    return sorted(picked, key=lambda item: order[item.id])

"""SessionState: everything one shopping session knows. Serializable to and from a plain dict for the
DynamoDB snapshot and the go-back history stack."""

from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field

from filters import Budget, HardFilters

HISTORY_DEPTH = 10
TRANSCRIPT_DEPTH = 10


@dataclass
class Commit:
    value: str
    confidence: float
    # 'decider' | 'stated' (parsed amount) | 'band' (budget_band answer) | 'recipient' (gender
    # implied by the recipient) | 'confirmed' (the shopper said yes to a change)
    source: str = 'decider'


@dataclass
class SessionState:
    session_id: str
    actor_id: str = 'anonymous'
    recipient: Commit | None = None
    gender: Commit | None = None
    department: Commit | None = None
    category: Commit | None = None
    budget: Budget | None = None
    budget_commit: Commit | None = None
    # Narrow-band answers from the latest turn: ranking context only, never filters.
    context: dict[str, tuple[str, float]] = field(default_factory=dict)
    shelf: list[dict] = field(default_factory=list)
    open_product: dict | None = None
    # The yes/no question awaiting an answer; the kinds are listed on policy.UnderstandPlan.offer.
    pending_offer: str | None = None
    cart: dict = field(default_factory=lambda: {'items': [], 'itemCount': 0, 'totalCents': 0})
    transcripts: list[str] = field(default_factory=list)
    remembered: list[str] = field(default_factory=list)
    columns: int = 4
    turn_seq: int = 0
    history: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ slots
    def committed(self, slot: str) -> Commit | None:
        return self.budget_commit if slot == 'budget' else getattr(self, slot)

    def is_open(self, slot: str) -> bool:
        return self.committed(slot) is None

    def hard_filters(self) -> HardFilters:
        return HardFilters(
            budget=self.budget,
            department=self.department.value if self.department else None,
            category=self.category.value if self.category else None,
            gender=self.gender.value if self.gender else None,
        )

    def add_transcript(self, text: str) -> None:
        self.transcripts = (self.transcripts + [text])[-TRANSCRIPT_DEPTH:]

    # ---------------------------------------------------------------- history
    # Go back restores the chips and the shelf, nothing else: never the cart, the turn
    # counter (cart idempotency keys use it), the layout or a pending offer. The
    # product panel closes, as it does on screen.
    VIEW = ('recipient', 'gender', 'department', 'category', 'budget', 'budget_commit', 'context', 'shelf')

    def _snapshot(self) -> dict:
        data = self.to_dict()
        return {k: data[k] for k in self.VIEW}

    def push_history(self) -> None:
        self.history = (self.history + [self._snapshot()])[-HISTORY_DEPTH:]

    def go_back(self) -> bool:
        if not self.history:
            return False
        previous = self.history.pop()
        restored = SessionState.from_dict({**self.to_dict(), **previous})
        for key in self.VIEW:
            setattr(self, key, getattr(restored, key))
        self.open_product = None
        self.pending_offer = None
        return True

    def start_over(self, initial_shelf: list[dict]) -> None:
        self.push_history()
        self.recipient = self.gender = self.department = self.category = self.budget_commit = None
        self.budget = None
        self.context = {}
        self.shelf = initial_shelf
        self.open_product = None
        self.pending_offer = None

    # ---------------------------------------------------------- serialization
    def to_dict(self) -> dict:
        data = asdict(self)
        data['budget'] = asdict(self.budget) if self.budget else None
        data['context'] = {k: list(v) for k, v in self.context.items()}
        return copy.deepcopy(data)

    @classmethod
    def from_dict(cls, data: dict) -> 'SessionState':
        data = copy.deepcopy(data)
        for slot in ('recipient', 'gender', 'department', 'category', 'budget_commit'):
            if data.get(slot):
                data[slot] = Commit(**data[slot])
        if data.get('budget'):
            data['budget'] = Budget(**data['budget'])
        data['context'] = {k: (v[0], float(v[1])) for k, v in (data.get('context') or {}).items()}
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in known})

"""Cases × candidates, independent of execution and domain scoring."""
from dataclasses import dataclass
import random
from typing import Any, Callable, Iterable, Mapping


@dataclass(frozen=True)
class Cell:
    """None is a valid assessed abstention; status distinguishes absent answers."""
    case: str
    candidate: str
    status: str = 'assessed'
    answer: Any = None
    reason: str = ''

    def __post_init__(self):
        if self.status not in {'assessed', 'missing', 'failed'}:
            raise ValueError('status must be assessed, missing or failed')
        if self.status != 'assessed' and self.answer is not None:
            raise ValueError('missing/failed cells cannot carry an answer')
        if self.status == 'failed' and not self.reason:
            raise ValueError('failed cells require a reason')


class Matrix:
    def __init__(self, cases: Iterable[str], candidates: Iterable[str], cells=()):
        self.cases, self.candidates = tuple(cases), tuple(candidates)
        if len(set(self.cases)) != len(self.cases) or len(set(self.candidates)) != len(self.candidates):
            raise ValueError('duplicate case or candidate')
        observed = {}
        for cell in cells:
            key = (cell.case, cell.candidate)
            if cell.case not in self.cases or cell.candidate not in self.candidates:
                raise ValueError(f'unknown cell {key}')
            if key in observed:
                raise ValueError(f'duplicate cell {key}')
            observed[key] = cell
        self.cells = tuple(observed.get((case, candidate), Cell(case, candidate, 'missing'))
                           for case in self.cases for candidate in self.candidates)

    def counts(self):
        return {candidate: {status: sum(c.candidate == candidate and c.status == status
                                       for c in self.cells)
                            for status in ('assessed', 'missing', 'failed')}
                for candidate in self.candidates}

    def score(self, references: Mapping[str, Any], scorer: Callable[[Any, Any], Any]):
        """Return all cells; invoke a flow-supplied scorer only with available pairs."""
        if set(references) - set(self.cases):
            raise ValueError('reference outside declared cases')
        return [{'case': c.case, 'candidate': c.candidate, 'status': c.status,
                 'answer': c.answer, 'reason': c.reason,
                 'reference_missing': c.case not in references,
                 'score': scorer(c.answer, references[c.case])
                 if c.status == 'assessed' and c.case in references else None}
                for c in self.cells]


def review_sample(matrix: Matrix, *, size: int, seed: int,
                  failure: Callable[[Cell], bool] = lambda cell: False):
    """All failed/missing/flagged cells plus `size` random remaining cells.

    The sample may exceed size. It is a diagnostic sample, not an unbiased
    accuracy estimate. Canonical ordering makes it independent of cell input order.
    """
    if size < 0:
        raise ValueError('size must be nonnegative')
    ordered = sorted(matrix.cells, key=lambda c: (c.case, c.candidate))
    required = [c for c in ordered if c.status != 'assessed' or failure(c)]
    required_keys = {(c.case, c.candidate) for c in required}
    remainder = [c for c in ordered if (c.case, c.candidate) not in required_keys]
    draw = random.Random(seed).sample(remainder, min(size, len(remainder)))
    return required + sorted(draw, key=lambda c: (c.case, c.candidate))

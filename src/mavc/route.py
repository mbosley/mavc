"""One local prediction, then one frontier call if needed; no retries or loaders."""
from dataclasses import dataclass
import math
from typing import Any, Callable


@dataclass(frozen=True)
class Prediction:
    answer: Any = None
    confidence: float | None = None
    abstained: bool = False


@dataclass(frozen=True)
class Routed:
    action_id: str
    answer: Any
    route: str
    model: str
    confidence: float | None
    cutoff: float
    fallback_reason: str | None = None
    local_error: str | None = None
    status: str = 'completed'
    error: str | None = None


class RouteFailure(RuntimeError):
    """Frontier failure, with the failed route record available to the caller."""
    def __init__(self, result: Routed):
        self.result = result
        super().__init__(result.error)


def route(action_id: str, input: Any, *, local: Callable, frontier: Callable,
          cutoff: float, local_model: str, frontier_model: str,
          valid: Callable = lambda answer: answer is not None) -> Routed:
    """Callbacks receive input only. A confidence exactly at cutoff is selected.

    `valid(answer)` supplies the flow's output check for both models. Local
    failure/abstention/invalidity falls back once. Frontier failure or invalidity
    raises RouteFailure, chained to the cause; its result never contains a
    rescued local answer. Serialize returned results (or failure.result) with
    dataclasses.asdict into the caller's existing output/record.
    """
    try:
        valid_cutoff = not isinstance(cutoff, bool) and math.isfinite(cutoff) and 0 <= cutoff <= 1
    except (TypeError, ValueError, OverflowError):
        valid_cutoff = False
    if not valid_cutoff:
        raise ValueError('cutoff must be finite in [0, 1]')
    confidence, reason, local_error = None, None, None
    try:
        prediction = local(input)
    except Exception as exc:
        reason, local_error = 'local_failure', f'{type(exc).__name__}: {exc}'
    else:
        if not isinstance(prediction, Prediction):
            reason = 'invalid_output'
        elif prediction.abstained or prediction.answer is None:
            reason = 'abstention'
        else:
            try:
                p = prediction.confidence
                if isinstance(p, bool) or p is None or not math.isfinite(p) or not 0 <= p <= 1:
                    reason = 'invalid_confidence'
                else:
                    confidence = float(p)
            except (TypeError, ValueError, OverflowError):
                reason = 'invalid_confidence'
            if reason is None:
                try:
                    accepted = bool(valid(prediction.answer))
                except Exception as exc:
                    accepted = False
                    local_error = f'{type(exc).__name__}: {exc}'
                if not accepted:
                    reason = 'invalid_output'
                elif confidence < cutoff:
                    reason = 'low_confidence'
                else:
                    return Routed(action_id, prediction.answer, 'local', local_model, confidence, cutoff)
    try:
        answer = frontier(input)
        if not valid(answer):
            raise ValueError('frontier output failed the flow output check')
    except Exception as exc:
        result = Routed(action_id, None, 'frontier', frontier_model, confidence, cutoff,
                        reason, local_error, 'failed', f'{type(exc).__name__}: {exc}')
        raise RouteFailure(result) from exc
    return Routed(action_id, answer, 'frontier', frontier_model, confidence, cutoff, reason, local_error)

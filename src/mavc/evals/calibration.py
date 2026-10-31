"""Fit temperature and cutoff on validation; evaluate a fixed policy on test."""
from dataclasses import dataclass
import numpy as np
from scipy.optimize import minimize_scalar
from scipy.special import logsumexp, softmax
from .metrics import label_indices, probability_array, selective_metrics


def scaled_probabilities(p, temperature=1, *, log_probabilities=None):
    p = probability_array(p)
    p = p / p.sum(1, keepdims=True)
    if not np.isfinite(temperature) or temperature <= 0:
        raise ValueError('temperature must be finite and positive')
    if log_probabilities is not None:
        logs = np.asarray(log_probabilities, dtype=float)
        if logs.shape != p.shape or not np.isfinite(logs).all():
            raise ValueError('log probabilities must be finite and match probabilities')
        if not np.allclose(softmax(logs, axis=1), p, atol=1e-5, rtol=1e-5):
            raise ValueError('stored probabilities and log probabilities disagree')
    else:
        if (p <= 0).any():
            raise ValueError('zero probabilities require stable stored log probabilities')
        logs = np.log(p)
    return p if temperature == 1 else softmax(logs/temperature, axis=1)


@dataclass(frozen=True)
class Validation:
    """Caller explicitly names the cohort; test data has no fitting entrypoint."""
    ids: tuple[str, ...]
    labels: tuple
    probabilities: object
    targets: tuple
    log_probabilities: object = None
    split: str = 'validation'

    def __post_init__(self):
        if self.split != 'validation':
            raise ValueError('policy fitting requires validation data')
        if not self.ids or len(set(self.ids)) != len(self.ids):
            raise ValueError('validation IDs must be nonempty and unique')
        p = probability_array(self.probabilities, len(self.labels))
        if len(p) != len(self.ids) or len(self.targets) != len(self.ids):
            raise ValueError('validation rows, IDs and targets must align')
        label_indices(self.targets, self.labels)
        scaled_probabilities(p, log_probabilities=self.log_probabilities)


@dataclass(frozen=True)
class Policy:
    temperature: float
    cutoff: float | None
    labels: tuple
    validation_ids: tuple[str, ...]
    validation_nll_before: float
    validation_nll_after: float
    thresholds: tuple[dict, ...]


def fit_policy(validation: Validation, *, cutoffs=(.8, .9, .95, .96, .97, .98, .99), target=.97):
    if not isinstance(validation, Validation) or validation.split != 'validation':
        raise ValueError('policy fitting requires Validation')
    if not 0 <= target <= 1 or not cutoffs or any(not 0 <= c <= 1 for c in cutoffs):
        raise ValueError('invalid cutoff or agreement target')
    p = probability_array(validation.probabilities, len(validation.labels))
    logs = np.log(p) if validation.log_probabilities is None else np.asarray(validation.log_probabilities, dtype=float)
    y = label_indices(validation.targets, validation.labels)

    def nll(log_temperature):
        z = logs / np.exp(log_temperature)
        return float((logsumexp(z, axis=1)-z[np.arange(len(y)), y]).mean())

    fit = minimize_scalar(nll, bounds=(-5, 5), method='bounded', options={'xatol': 1e-12})
    if not fit.success or not -4.99 < fit.x < 4.99 or fit.fun > nll(0):
        raise ValueError('temperature fit failed or reached search boundary')
    temperature = float(np.exp(fit.x))
    q = softmax(logs/temperature, axis=1)
    correct = q.argmax(1) == y
    thresholds = []
    for cutoff in cutoffs:
        mask = q.max(1) >= cutoff
        n, matches = int(mask.sum()), int(correct[mask].sum())
        thresholds.append({'cutoff': cutoff, 'n': n, 'matches': matches,
                           'coverage': n/len(y), 'agreement': matches/n if n else None})
    eligible = [r for r in thresholds if r['n'] and r['agreement'] >= target]
    cutoff = min(r['cutoff'] for r in eligible) if eligible else None
    return Policy(temperature, cutoff, tuple(validation.labels), tuple(validation.ids),
                  nll(0), float(fit.fun), tuple(thresholds))


def evaluate_test(policy: Policy, ids, p, references, *, log_probabilities=None):
    """No fitting or cutoff adjustment here. References use explicit row indices."""
    ids = tuple(ids)
    if len(ids) != len(p) or len(set(ids)) != len(ids):
        raise ValueError('test IDs must be unique and align with probabilities')
    if set(ids) & set(policy.validation_ids):
        raise ValueError('validation/test ID leakage')
    q = scaled_probabilities(p, policy.temperature, log_probabilities=log_probabilities)
    return selective_metrics(q, references, labels=policy.labels, cutoff=policy.cutoff)

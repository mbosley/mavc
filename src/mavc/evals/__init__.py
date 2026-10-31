"""Offline comparisons independent of runner internals and flow label rules.

Install the mavc[evals] extra for probability/statistics functions.
None in an assessed Cell is a valid abstention. Missing references are absent
mapping keys. Never treat schema/quote checks or observer agreement as accuracy.
"""
from .completeness import completeness
from .matrix import Cell, Matrix, review_sample
from .stability import multiset_overlap, multiset_stability, overlap, stability

# Load statistics dependencies only when a statistics function is requested.
_STATISTICS = {'agreement', 'probability_metrics', 'selective_metrics', 'wilson'}
_CALIBRATION = {'Policy', 'Validation', 'evaluate_test', 'fit_policy', 'scaled_probabilities'}
__all__ = ['Cell', 'Matrix', 'review_sample', 'completeness', 'overlap', 'stability', 'multiset_overlap', 'multiset_stability',
           *sorted(_STATISTICS), *sorted(_CALIBRATION)]


def __getattr__(name):
    if name in _STATISTICS:
        from importlib import import_module
        module = import_module('.metrics', __name__)
        globals().update({key: getattr(module, key) for key in _STATISTICS})
        return globals()[name]
    if name in _CALIBRATION:
        from importlib import import_module
        module = import_module('.calibration', __name__)
        globals().update({key: getattr(module, key) for key in _CALIBRATION})
        return globals()[name]
    raise AttributeError(name)

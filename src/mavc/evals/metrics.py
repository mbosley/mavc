"""Categorical and probability comparisons; references are observations, not truth."""
import math
import numpy as np
from sklearn.metrics import cohen_kappa_score, confusion_matrix, precision_recall_fscore_support

DEFAULT_EDGES = (0, .2, .4, .6, .8, .9, 1)


def wilson(matches, n):
    if not 0 <= matches <= n:
        raise ValueError('require 0 <= matches <= n')
    if not n:
        return None
    p, z = matches / n, 1.96
    centre = (p + z*z/(2*n)) / (1 + z*z/n)
    half = z*math.sqrt(p*(1-p)/n + z*z/(4*n*n)) / (1 + z*z/n)
    return [centre-half, centre+half]


def label_indices(values, labels):
    labels = tuple(labels)
    if not labels or len(set(labels)) != len(labels):
        raise ValueError('labels must be nonempty and unique')
    index = {label: i for i, label in enumerate(labels)}
    try:
        return np.array([index[v] for v in values], dtype=int)
    except KeyError as exc:
        raise ValueError(f'unknown label {exc.args[0]!r}') from exc


def agreement(targets, predictions, *, labels):
    """Explicit label order; include None in labels to count valid abstentions."""
    y, pred = label_indices(targets, labels), label_indices(predictions, labels)
    if len(y) != len(pred):
        raise ValueError('unpaired observations')
    n = len(y)
    indices = list(range(len(labels)))
    if n:
        precision, recall, f1, support = precision_recall_fscore_support(
            y, pred, labels=indices, zero_division=0)
        confusion = confusion_matrix(y, pred, labels=indices)
        # Kappa is undefined when both observers use only the same one category.
        kappa = None if len(set(y) | set(pred)) == 1 else float(
            cohen_kappa_score(y, pred, labels=indices))
    else:
        precision = recall = f1 = support = np.zeros(len(labels))
        confusion = np.zeros((len(labels), len(labels)), dtype=int)
        kappa = None
    matches = int((y == pred).sum())
    return {'n': n, 'matches': matches, 'agreement': matches/n if n else None,
            'wilson_95': wilson(matches, n), 'kappa': kappa,
            'types': {label: {'precision': float(precision[i]), 'recall': float(recall[i]),
                              'f1': float(f1[i]), 'n': int(support[i])}
                      for i, label in enumerate(labels)}, 'confusion': confusion.tolist()}


def probability_array(p, width=None):
    p = np.asarray(p, dtype=float)
    if p.ndim != 2 or not p.shape[1] or (width is not None and p.shape[1] != width):
        raise ValueError('require a cases × labels probability matrix')
    if not np.isfinite(p).all() or (p < 0).any() or (p > 1).any():
        raise ValueError('probabilities must be finite in [0, 1]')
    if not np.allclose(p.sum(1), 1, atol=1e-5, rtol=0):
        raise ValueError('probabilities must sum to one')
    return p


def probability_metrics(p, targets, *, labels, edges=DEFAULT_EDGES):
    p = probability_array(p, len(labels))
    y = label_indices(targets, labels)
    if len(y) != len(p):
        raise ValueError('unpaired probabilities and targets')
    edges = tuple(edges)
    if len(edges) < 2 or edges[0] != 0 or edges[-1] != 1 or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('bin edges must increase from 0 to 1')
    pred = p.argmax(1)
    result = agreement(targets, [labels[i] for i in pred], labels=labels)
    n = len(y)
    confidence, correct = p.max(1), pred == y
    reliability = []
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (confidence >= low) & ((confidence <= high) if high == 1 else (confidence < high))
        count = int(mask.sum())
        reliability.append({'low': low, 'high': high, 'n': count,
                            'confidence': float(confidence[mask].mean()) if count else None,
                            'agreement': float(correct[mask].mean()) if count else None})
    result.update(brier=float(np.square(p-np.eye(len(labels))[y]).sum(1).mean()) if n else None,
                  nll=float(-np.log(np.maximum(p[np.arange(n), y], np.finfo(float).tiny)).mean()) if n else None,
                  ece=sum(r['n']/n*abs(r['confidence']-r['agreement']) for r in reliability if r['n']) if n else None,
                  reliability=reliability)
    return result


def selective_metrics(p, references, *, labels, cutoff):
    """References map names to {row_index: label}; absent entries stay missing."""
    p = probability_array(p, len(labels))
    if cutoff is not None and not 0 <= cutoff <= 1:
        raise ValueError('cutoff must be in [0, 1] or None')
    selected = p.max(1) >= cutoff if cutoff is not None else np.zeros(len(p), dtype=bool)
    n = int(selected.sum())
    result = {'n': n, 'total': len(p), 'coverage': n/len(p) if len(p) else None,
              'coverage_wilson_95': wilson(n, len(p)), 'cutoff': cutoff,
              'selected_indices': np.flatnonzero(selected).tolist(), 'references': {}}
    predictions = [labels[i] for i in p.argmax(1)]
    for name, observed in references.items():
        if any(not isinstance(i, int) or not 0 <= i < len(p) for i in observed):
            raise ValueError('reference index outside cases')
        label_indices(observed.values(), labels)
        indices = [i for i in range(len(p)) if selected[i] and i in observed]
        hits = sum(predictions[i] == observed[i] for i in indices)
        result['references'][name] = {'n': len(indices), 'matches': hits,
            'agreement': hits/len(indices) if indices else None, 'wilson_95': wilson(hits, len(indices)),
            'reference_n': len(observed), 'reference_missing': len(p)-len(observed),
            'selected_reference_missing': n-len(indices)}
    return result

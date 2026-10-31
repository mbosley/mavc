"""Repeat stability is descriptive overlap, not scientific correctness."""
from itertools import combinations


def overlap(left, right, *, key=lambda item: item):
    """Exact set overlap; callers supply domain projections such as source spans."""
    a, b = set(map(key, left)), set(map(key, right))
    common, union = len(a & b), len(a | b)
    return {'left_n': len(a), 'right_n': len(b), 'common': common,
            'left_only': len(a-b), 'right_only': len(b-a),
            'jaccard': common/union if union else None,
            'left_coverage': common/len(a) if a else None,
            'right_coverage': common/len(b) if b else None}


def stability(runs, *, key=lambda item: item):
    return [{'left': left, 'right': right, **overlap(runs[left], runs[right], key=key)}
            for left, right in combinations(sorted(runs), 2)]


def multiset_overlap(left, right, *, key=lambda item: item):
    """Counter intersection/union preserves each duplicate occurrence (Stage B)."""
    from collections import Counter
    a, b = Counter(map(key, left)), Counter(map(key, right))
    common, union = sum((a & b).values()), sum((a | b).values())
    x, y = sum(a.values()), sum(b.values())
    return {'left_n': x, 'right_n': y, 'common': common,
            'left_only': x-common, 'right_only': y-common,
            'jaccard': common/union if union else None,
            'left_coverage': common/x if x else None,
            'right_coverage': common/y if y else None,
            'removed': list((a-b).elements()), 'added': list((b-a).elements())}


def multiset_stability(runs, *, key=lambda item: item):
    return [{'left': left, 'right': right, **multiset_overlap(runs[left], runs[right], key=key)}
            for left, right in combinations(sorted(runs), 2)]

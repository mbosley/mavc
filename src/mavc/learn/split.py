"""Whole-group splits and leakage checks, including explicit held-out countries."""
import random
import math
from .data import Example


def check_splits(metadata, *, group_fields=('document_id','pdf_sha256'),
                 heldout_countries=None, heldout_families=None,
                 test_splits=('test','country_test','benchmark')):
    """Streaming validation; remembers identities, not source text or targets."""
    if not group_fields:
        raise ValueError('at least one grouping field is required')
    seen = {}
    for meta in metadata:
        split = meta['split']
        if not isinstance(split,str) or not split:
            raise ValueError('split must be a nonempty name')
        for field in group_fields:
            value = meta.get(field)
            if value is None:
                raise ValueError(f'missing grouping field {field}')
            key = (field, value)
            if key in seen and seen[key] != split:
                raise ValueError(f'{field} leakage: {value!r} in {seen[key]} and {split}')
            seen[key] = split
        for field, heldout in [('country',heldout_countries),('family',heldout_families)]:
            if heldout and meta.get(field) in heldout and split != heldout[meta[field]]:
                raise ValueError(f'held-out {field} placed in {split}')
        if (meta.get('test_only') or meta.get('label_source') in {'human_review','model_review','review'}) and split not in test_splits:
            raise ValueError('review labels are test-only')
    return {'groups':len(seen),'group_fields':list(group_fields)}


def split(examples, *, fractions=None, seed=0,
          group_fields=('document_id','pdf_sha256'), heldout_countries=None,
          heldout_families=None):
    """Seeded whole-group assignment, joining aliases shared in ANY grouping field.

    Rows are materialized for new split design. For an existing large campaign,
    preserve its committed memberships and use streaming check_splits/export.
    Fractions target groups, not row counts; domain stratification stays outside.
    """
    examples = list(examples)
    fractions = fractions or {'train':.8,'validation':.1,'test':.1}
    if not fractions or any(not math.isfinite(v) or v < 0 for v in fractions.values()) or abs(sum(fractions.values())-1) > 1e-9:
        raise ValueError('split fractions must be nonnegative and sum to one')
    parents = list(range(len(examples)))
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    seen = {}
    for i, example in enumerate(examples):
        for field in group_fields:
            if example.metadata.get(field) is None:
                raise ValueError(f'missing grouping field {field}')
            key = (field, example.metadata[field])
            if key in seen:
                parents[root(i)] = root(seen[key])
            seen[key] = i
    groups = {}
    for i in range(len(examples)):
        groups.setdefault(root(i),[]).append(i)
    ordered = sorted(groups.values(),key=lambda group:sorted(
        tuple(str(examples[i].metadata[field]) for field in group_fields) for i in group))
    rng = random.Random(seed)
    assignments = {}
    for group in ordered:
        reserved = set()
        for i in group:
            meta = examples[i].metadata
            for field, mapping in [('country',heldout_countries),('family',heldout_families)]:
                if mapping and meta.get(field) in mapping:
                    reserved.add(mapping[meta[field]])
        if any(examples[i].metadata.get('test_only') or examples[i].metadata.get('label_source') in {'human_review','model_review','review'} for i in group) and not reserved:
            reserved.add('test')
        if len(reserved) > 1:
            raise ValueError('connected group has conflicting held-out assignments')
        choice = next(iter(reserved)) if reserved else None
        if choice is None:
            draw, cumulative = rng.random(), 0
            for name, fraction in fractions.items():
                cumulative += fraction
                if draw < cumulative:
                    choice = name
                    break
        for i in group:
            assignments[i] = choice
    result = [Example(e.input,e.target,{**e.metadata,'split':assignments[i]}) for i,e in enumerate(examples)]
    check_splits((e.metadata for e in result), group_fields=group_fields,
                 heldout_countries=heldout_countries,heldout_families=heldout_families)
    return result

"""Stream projected examples and keep a decision row for every candidate."""
from collections import Counter
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
from .split import check_splits


def json_line(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'


def export(admissions, folder, *, format_example=None,
           group_fields=('document_id','pdf_sha256'), heldout_countries=None,
           heldout_families=None):
    """Write full included rows and admissions.jsonl; metadata defines the split.

    Each rejected candidate remains counted without being a training target.
    A failed export leaves no manifest, so it cannot look like a complete dataset.
    The destination must be new; never rewrite an existing campaign.
    """
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=False)
    formatter = format_example or (lambda e: {'input':e.input,'target':e.target,'metadata':e.metadata})
    counts, by_split, ids, groups = Counter(), Counter(), set(), {}
    files, hashes = {}, {}
    with ExitStack() as stack:
        journal = stack.enter_context((folder/'admissions.jsonl').open('w',encoding='utf-8'))
        for admission in admissions:
            if admission.id in ids:
                raise ValueError(f'duplicate candidate ID {admission.id}')
            ids.add(admission.id)
            status = admission.decision.status
            if status == 'included' and admission.example is None:
                raise ValueError('included candidate has no target')
            if status != 'included' and admission.example is not None:
                raise ValueError('excluded/quarantined candidate cannot carry a target')
            counts[status] += 1
            journal.write(json_line({'id':admission.id,**asdict(admission.decision),'metadata':admission.metadata}))
            if status != 'included':
                continue
            example = admission.example
            meta = admission.metadata
            if any(example.metadata.get(field) != meta.get(field) for field in (*group_fields,'split','label_source')):
                raise ValueError('export metadata differs from projected example')
            name = meta['split']
            if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',name):
                raise ValueError('invalid split name')
            # Include prior identities to check leakage without holding example bodies.
            for field in group_fields:
                value = meta.get(field)
                key = (field,value)
                if value is None or (key in groups and groups[key] != name):
                    raise ValueError(f'missing or leaked grouping field {field}: {value!r}')
                groups[key] = name
            check_splits([meta],group_fields=group_fields,heldout_countries=heldout_countries,
                         heldout_families=heldout_families)
            if name not in files:
                files[name] = stack.enter_context((folder/(name+'.jsonl')).open('w',encoding='utf-8'))
                hashes[name] = hashlib.sha256()
            line = json_line(formatter(example))
            files[name].write(line)
            hashes[name].update(line.encode())
            by_split[name] += 1
    manifest = {'candidates':len(ids),'decisions':{s:counts[s] for s in ['included','excluded','quarantined']},
                'splits':dict(by_split),'group_fields':list(group_fields),
                'sha256':{name:hashes[name].hexdigest() for name in files}}
    (folder/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    return manifest

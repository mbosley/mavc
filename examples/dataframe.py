"""Turn the tutorial's completed per-item results into a table; requires mavc[examples]."""
from pathlib import Path
import sys

import pandas as pd
from mavc.record import read_json, resolve
from mavc.tree import walk_nodes


def labels_frame(folder: str | Path) -> pd.DataFrame:
    record = read_json(Path(folder) / 'record.json')
    rows = [{'node': name, **resolve(folder, row['output']).value}
            for name, row in walk_nodes(record, leaves_only=True)
            if row.get('position') == 'Review.LabelAll.Classify' and row['outcome']['status'] == 'completed']
    return pd.DataFrame(rows, columns=['node', 'id', 'label'])


if __name__ == '__main__':
    print(labels_frame(sys.argv[1] if len(sys.argv) > 1 else 'runs/tutorial').to_string(index=False))

"""A model per item, then a code check, using only the bundled offline stub."""
from pathlib import Path
import sys
from typing import Literal

from pydantic import BaseModel, RootModel
from mavc import Operation, Rejected, Runner, for_each, sequence, verify
from mavc.stub import classify


class Label(BaseModel):
    id: str
    label: Literal['question', 'statement']


class Classify(Operation):
    """Label the supplied text as a question or statement and preserve its ID."""
    class Input(BaseModel):
        id: str
        text: str
    Output = Label


class Check(Operation):
    """Check unique nonempty IDs and count the accepted labels."""
    Input = RootModel[list[Label]]
    class Output(BaseModel):
        total: int
        counts: dict[str, int]

    def run(self, children, input):
        rows = input.root
        ids = [row.id for row in rows]
        if any(not identifier.strip() for identifier in ids) or len(set(ids)) != len(ids):
            raise Rejected('Each result needs a unique nonempty ID')
        return {'total': len(rows), 'counts': {
            label: sum(row.label == label for row in rows) for label in ('question', 'statement')}}


def main():
    flow = sequence('Review', [for_each('LabelAll', Classify(caller=classify), key=lambda row: row['id']), Check()])
    declaration = {'flow_id': 'text-labels/v1', 'positions': {
        'Review.LabelAll.Classify': {'provider': 'stub', 'model': 'punctuation-v1', 'reasoning': 'none'},
    }}
    data = [{'id': 'one', 'text': 'Is the record complete?'},
            {'id': 'two', 'text': 'The input is supplied.'},
            {'id': 'three', 'text': 'Can we inspect the evidence?'}]
    folder = Path(sys.argv[1]) if len(sys.argv) > 1 else Path('runs/tutorial')
    with Runner(folder, declaration, flow=flow) as runner:
        answer = flow(runner, data)
        runner.finish(answer)
    assert verify(folder)['completed']
    print(answer)  # {'total': 3, 'counts': {'question': 2, 'statement': 1}}


if __name__ == '__main__':
    main()

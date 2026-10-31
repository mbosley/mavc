"""Defining files and explicitly named helpers, without an import graph."""
import hashlib
import inspect
from functools import partial
from pathlib import Path


def _target(value):
    while isinstance(value, partial):
        value = value.func
    if not (inspect.isroutine(value) or inspect.isclass(value) or inspect.ismodule(value)):
        value = type(value).__call__
    return value


def _source(value):
    if isinstance(value, (str, Path)):
        path, label = Path(value), Path(value).name
    else:
        value = _target(value)
        label = '.'.join(filter(None, (getattr(value, '__module__', ''),
            getattr(value, '__qualname__', getattr(value, '__name__', type(value).__qualname__)))))
        try:
            filename = inspect.getsourcefile(value)
        except TypeError:
            filename = None
        if not filename:
            raise ValueError(f'No defining source for {label}; supply sources=(source_path, helper_callable, ...) '
                             'or a legacy implementations identity')
        path = Path(filename)
        code = getattr(value, '__code__', None)
        if code:
            column = next((p[2] for p in code.co_positions() if p[2] not in (None, 0)), 0)
            label += f'@{code.co_firstlineno}:{column}'
        label += '/' + path.name
    if not path.is_file():
        raise ValueError(f'No readable defining source {path}; supply an explicit sources file')
    return label, hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(operation, sources=()):
    """Hash operation source and declared paths/modules/callables; data stays input."""
    result = {}
    for index, source in enumerate(sources):
        label, value = _source(source)
        result[f'source/{index}/{label}'] = value
    if operation is not None:
        try:
            label, value = _source(operation)
        except ValueError:
            if not result:
                raise
            target = _target(operation)
            label = f"{getattr(target, '__module__', '')}.{getattr(target, '__qualname__', type(target).__qualname__)}"
            value = hashlib.sha256(label.encode()).hexdigest()
            label = 'explicit-boundary/' + label
        result['operation/' + label] = value
    return result

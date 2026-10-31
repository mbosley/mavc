"""Traverse one canonical tree, without importing runtime dependencies."""


def walk_nodes(record, *, leaves_only=False):
    """Yield stable scientific IDs and nodes from JSON or typed run records."""
    nodes = record['nodes'] if isinstance(record, dict) else record.nodes
    for name, node in nodes.items():
        children = node.get('children', {}) if isinstance(node, dict) else node.children
        composite = node.get('composition', '') if isinstance(node, dict) else node.composition
        if not leaves_only or not composite:
            yield name, node
        yield from walk_nodes({'nodes': children}, leaves_only=leaves_only)

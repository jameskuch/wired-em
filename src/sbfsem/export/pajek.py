"""Network exports: Pajek ``.net``, link lists and edge tables.

Ports ``util/export/pajekNET.m``, ``util/export/linklistTXT.m`` and
``util/export/linklistTXT2.m``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["write_pajek", "write_link_list", "write_edge_csv"]


def write_pajek(network, path: str | Path, *, labels: dict[int, str] | None = None) -> Path:
    """Write a connectivity network as a Pajek ``.net`` file."""
    labels = labels or {}
    path = Path(path)
    ids = network.neuron_ids
    index = {node: i + 1 for i, node in enumerate(ids)}
    lines = [f"*Vertices {len(ids)}"]
    for node in ids:
        label = labels.get(node) or f"c{node}"
        lines.append(f'{index[node]} "{label}"')
    lines.append("*Arcs")
    for source, target, data in network.graph.edges(data=True):
        lines.append(f"{index[int(source)]} {index[int(target)]} {int(data.get('weight', 1))}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_link_list(links: pd.DataFrame, path: str | Path, *, delimiter: str = "\t") -> Path:
    """Write a link table as plain text, one synapse per line."""
    path = Path(path)
    frame = links if links is not None else pd.DataFrame()
    frame.to_csv(path, sep=delimiter, index=False)
    return path


def write_edge_csv(network, path: str | Path) -> Path:
    """Write the aggregated edge list as CSV."""
    rows = [
        {
            "SourceID": int(s),
            "TargetID": int(t),
            "Weight": int(d.get("weight", 1)),
            "Types": ";".join(sorted(set(d.get("types", [])))),
        }
        for s, t, d in network.graph.edges(data=True)
    ]
    path = Path(path)
    pd.DataFrame(rows).to_csv(path, index=False)
    return path

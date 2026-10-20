"""Saved documents, attached renders and the shared library: how they are written.

A held Enter (or a double click) on the Save dialog's button writes one document
twice while the first write is in the air.  Every store wrote through the same
temporary file for a document, so the second writer replaced it from under the
first, whose own ``replace`` then failed with a 500 (found 2026-10-16: one of
two concurrent PUTs to ``/api/saves/<folder>/<name>`` answered
FileNotFoundError).  Each writer has its own temporary file now.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from sbfsem_web.saves import CellRenderStore, LibraryStore, SaveStore


def _interleave(monkeypatch, second_writer):
    """Run ``second_writer`` once, in the middle of the first temporary write.

    That is the worst moment for a fixed temporary name: the first writer has
    written its bytes and not yet replaced the target, the second writes and
    replaces, and the first then has no file to replace with.
    """
    real = Path.write_bytes
    state = {"ran": False}

    def write_bytes(self, data):
        written = real(self, data)
        if self.name.endswith(".tmp") and not state["ran"]:
            state["ran"] = True
            second_writer()
        return written

    monkeypatch.setattr(Path, "write_bytes", write_bytes)
    return state


def test_two_saves_of_one_document_in_flight_both_succeed(tmp_path, monkeypatch):
    store = SaveStore(tmp_path)
    store.create_folder("runs")
    state = _interleave(monkeypatch, lambda: store.write("runs", "c1178", {"second": True}))
    store.write("runs", "c1178", {"first": True})
    assert state["ran"]
    assert store.read("runs", "c1178") in ({"first": True}, {"second": True})
    assert list((tmp_path / "runs").glob("*.tmp")) == [], "no temporary file is left behind"
    assert [f.name for f in store.list_files("runs")] == ["c1178"]


def test_two_attached_renders_of_one_cell_in_flight_both_succeed(tmp_path, monkeypatch):
    store = CellRenderStore(tmp_path)
    state = _interleave(monkeypatch, lambda: store.set("RC1", 410, {"pathway": "voxel2"}))
    store.set("RC1", 410, {"pathway": "original"})
    assert state["ran"]
    assert store.get("RC1", 410) in ({"pathway": "voxel2"}, {"pathway": "original"})
    assert list(tmp_path.rglob("*.tmp")) == []


def test_the_render_store_itself_refuses_an_id_that_is_not_a_32_bit_structure_id(tmp_path):
    """The route's ``CellId`` constraint is the first line; the store is the one that
    names a file, and the only one a caller other than the route would meet."""
    from sbfsem_web.saves import MAX_CELL_ID, NameError_

    store = CellRenderStore(tmp_path)
    render = {"pathway": "voxel2"}
    for bad in (0, -1, -(2**31), MAX_CELL_ID + 1, 2**63, True):
        assert store.get("RC1", bad) is None, bad
        assert store.delete("RC1", bad) is False, bad
        with pytest.raises(NameError_):
            store.set("RC1", bad, render)
    assert list(tmp_path.rglob("*.json")) == [], "nothing was written for any of them"
    # Both ends of the range are structure IDs.
    for good in (1, MAX_CELL_ID):
        store.set("RC1", good, render)
        assert store.get("RC1", good) == render
        assert store.delete("RC1", good) is True


def test_two_library_entries_of_one_name_in_flight_both_succeed(tmp_path, monkeypatch):
    store = LibraryStore(tmp_path)
    state = _interleave(monkeypatch, lambda: store.set("felt", {"a": 2}))
    store.set("felt", {"a": 1})
    assert state["ran"]
    assert store.get("felt")["a"] in (1, 2)
    assert list(tmp_path.rglob("*.tmp")) == []


def test_a_write_that_fails_leaves_neither_a_half_file_nor_a_temporary(tmp_path, monkeypatch):
    store = SaveStore(tmp_path)
    store.create_folder("runs")
    store.write("runs", "doc", {"good": True})

    def refuse(self, target):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "replace", refuse)
    with pytest.raises(OSError, match="disk full"):
        store.write("runs", "doc", {"good": False})
    monkeypatch.undo()
    assert store.read("runs", "doc") == {"good": True}, "the earlier document is untouched"
    assert list((tmp_path / "runs").glob("*.tmp")) == []


def test_many_writers_of_one_document_leave_one_whole_document(tmp_path):
    store = SaveStore(tmp_path)
    store.create_folder("runs")
    errors: list[BaseException] = []
    start = threading.Barrier(8)

    def save(n):
        start.wait()
        try:
            for _ in range(20):
                store.write("runs", "doc", {"writer": n, "pad": "x" * 2000})
        except BaseException as error:  # noqa: BLE001 - the thread must report it, not die silently
            errors.append(error)

    threads = [threading.Thread(target=save, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    text = (tmp_path / "runs" / "doc.json").read_text("utf-8")
    assert json.loads(text)["writer"] in range(8), "whole, parseable"
    assert list((tmp_path / "runs").glob("*.tmp")) == []

"""Named JSON documents stored on the server, under one confined directory.

Every path this module produces is built from a validated name and then checked
again against the base directory after resolution, because a single validation
regex is one typo away from a directory traversal.  Sign-in is optional (the
built-in user list and the Viking Identity Server are each off unless
configured, and a local checkout runs open), and anyone who is signed in may
write: the quotas below are the only thing bounding what one caller can
write, so they are enforced, not advisory.
"""

from __future__ import annotations

import json
import logging
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["CellRenderStore", "LibraryStore", "SaveStore", "SaveError", "NameError_", "QuotaError"]

#: Letters, digits, space, dot, dash, underscore; must start with a letter or
#: digit so nothing can be a dotfile, and short enough for any filesystem.
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$")

#: Reserved on Windows, where creating one of these fails in confusing ways.
#: Harmless on the Linux server, but the app is developed on other platforms.
_RESERVED = frozenset({
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
})

#: Bounds on what one anonymous caller can consume.
MAX_FOLDERS = 100
MAX_FILES_PER_FOLDER = 200
MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024


class SaveError(Exception):
    """Base for anything the caller did wrong."""


class NameError_(SaveError):
    """A folder or file name that will not be accepted."""


class QuotaError(SaveError):
    """A limit that would be exceeded."""


@dataclass(frozen=True)
class Entry:
    name: str
    bytes: int
    modified: str
    files: int | None = None  # folders only


def _write_atomic(path: Path, body: bytes) -> None:
    """Write ``body`` to ``path`` so that a reader sees the old file or the new one.

    The bytes go to a temporary file of this writer's own beside the target and
    replace it.  The temporary name used to be the same for every writer of a
    document (``<name>.json.tmp``): two saves of one document in flight at once
    (a held Enter, a double click on Save) wrote the same file, and the second
    ``replace`` found it already gone and failed with a 500.  Now each writer
    has its own, and the later replace simply wins.
    """
    tmp = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(body)
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _dumps(document: object, *, indent: int | None = None) -> bytes:
    """A document as JSON bytes, or a :class:`SaveError` saying why it cannot be.

    ``allow_nan=False`` makes the encoder refuse a NaN or an infinity (JSON
    cannot hold them); that used to escape as a bare ValueError and an HTTP 500.
    """
    try:
        return json.dumps(document, indent=indent, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError) as exc:
        raise SaveError(f"This cannot be saved as JSON ({exc})") from exc


def _validate(name: str, what: str) -> str:
    """Return `name` if it is a safe single path component."""
    raw = (name or "").strip()
    # Normalise first: a decomposed character can pass a regex and then compare
    # differently once the filesystem has its say.
    raw = unicodedata.normalize("NFC", raw)
    if not _NAME.match(raw):
        raise NameError_(
            f"{what} names may use letters, digits, spaces, dots, dashes and "
            "underscores, must start with a letter or digit, and be at most "
            "63 characters"
        )
    # Belt and braces: the regex already excludes these, but a `..` folder is
    # the failure that matters, so it is worth naming explicitly.
    if raw in (".", "..") or "/" in raw or "\\" in raw or raw.endswith("."):
        raise NameError_(f"{what} name is not allowed: {name!r}")
    if raw.split(".")[0].lower() in _RESERVED:
        raise NameError_(f"{name!r} is a reserved device name on Windows")
    return raw


class SaveStore:
    """JSON documents under ``base``, in one level of user-named folders."""

    def __init__(self, base: str | os.PathLike[str]):
        self.base = Path(base).resolve()

    def ensure_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)

    # -- path safety -------------------------------------------------------
    def _folder_path(self, folder: str) -> Path:
        path = (self.base / _validate(folder, "Folder")).resolve()
        # Re-check after resolution. Symlinks and normalisation happen here,
        # not in the regex, so this is the check that actually holds.
        if path != self.base and self.base not in path.parents:
            raise NameError_("folder resolves outside the save directory")
        return path

    def _file_path(self, folder: str, name: str) -> Path:
        stem = _validate(name.removesuffix(".json"), "File")
        path = (self._folder_path(folder) / f"{stem}.json").resolve()
        if self.base not in path.parents:
            raise NameError_("file resolves outside the save directory")
        return path

    # -- reading -----------------------------------------------------------
    def total_bytes(self) -> int:
        if not self.base.exists():
            return 0
        return sum(f.stat().st_size for f in self.base.rglob("*.json") if f.is_file())

    def list_folders(self) -> list[Entry]:
        self.ensure_base()
        out = []
        for d in sorted(self.base.iterdir()):
            if not d.is_dir():
                continue
            files = [f for f in d.glob("*.json") if f.is_file()]
            out.append(Entry(
                name=d.name,
                bytes=sum(f.stat().st_size for f in files),
                modified=_stamp(d),
                files=len(files),
            ))
        return out

    def list_files(self, folder: str) -> list[Entry]:
        path = self._folder_path(folder)
        if not path.is_dir():
            raise SaveError(f"No folder named {folder!r}")
        return [
            Entry(name=f.stem, bytes=f.stat().st_size, modified=_stamp(f))
            for f in sorted(path.glob("*.json")) if f.is_file()
        ]

    def read(self, folder: str, name: str) -> dict:
        path = self._file_path(folder, name)
        if not path.is_file():
            raise SaveError(f"No saved document named {name!r}")
        try:
            return json.loads(path.read_text("utf-8"))
        except ValueError as exc:  # JSONDecodeError and UnicodeDecodeError are both ValueErrors
            # A half-written file from before saves were atomic, or one edited by hand.
            raise SaveError(f"{name!r} is not a readable document ({exc})") from exc

    # -- writing -----------------------------------------------------------
    def create_folder(self, folder: str) -> Entry:
        self.ensure_base()
        path = self._folder_path(folder)
        if path.exists():
            raise SaveError(f"A folder named {path.name!r} already exists")
        if len(self.list_folders()) >= MAX_FOLDERS:
            raise QuotaError(f"At most {MAX_FOLDERS} folders")
        path.mkdir()
        return Entry(name=path.name, bytes=0, modified=_stamp(path), files=0)

    def write(self, folder: str, name: str, document: dict) -> Entry:
        path = self._file_path(folder, name)
        if not path.parent.is_dir():
            raise SaveError(f"No folder named {folder!r}")

        body = _dumps(document, indent=1)
        if len(body) > MAX_FILE_BYTES:
            raise QuotaError(
                f"{len(body) // 1024} kB exceeds the {MAX_FILE_BYTES // 1024} kB limit"
            )
        existing = path.stat().st_size if path.is_file() else 0
        if self.total_bytes() - existing + len(body) > MAX_TOTAL_BYTES:
            raise QuotaError(
                f"The save directory is full ({MAX_TOTAL_BYTES // 1024 // 1024} MB)"
            )
        if not path.is_file() and len(list(path.parent.glob("*.json"))) >= MAX_FILES_PER_FOLDER:
            raise QuotaError(f"At most {MAX_FILES_PER_FOLDER} documents per folder")

        # Write beside the target and replace, so an interrupted write cannot
        # leave a half-written document where a good one used to be.
        _write_atomic(path, body)
        return Entry(name=path.stem, bytes=len(body), modified=_stamp(path))

    def delete(self, folder: str, name: str) -> None:
        path = self._file_path(folder, name)
        if not path.is_file():
            raise SaveError(f"No saved document named {name!r}")
        path.unlink()

    def delete_folder(self, folder: str) -> None:
        path = self._folder_path(folder)
        if not path.is_dir():
            raise SaveError(f"No folder named {folder!r}")
        if any(path.iterdir()):
            raise SaveError("The folder is not empty")
        path.rmdir()


def _stamp(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat(timespec="seconds")


#: An attached render is one options dict; anything bigger is not a render.
MAX_RENDER_BYTES = 8 * 1024
#: Viking numbers structures as 32-bit database keys.
MAX_CELL_ID = 2**31 - 1
#: Attached renders on the server, in all.  Nobody attaches a render to this many
#: cells; the cap is what stops an anonymous caller's id loop filling the disk.
MAX_ATTACHED_RENDERS = 5000


class CellRenderStore:
    """Render settings attached to individual cells, on the server.

    One small JSON file per ``(source, cell id)`` under a confined directory,
    separate from :class:`SaveStore` so attached renders never appear in the
    workspace Open dialog.  The source is validated against the same name
    rules as save folders; the id must be a positive integer, so no path can
    escape the base.
    """

    def __init__(self, base: str | os.PathLike[str]):
        self.base = Path(base).resolve()

    def ensure_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, source: str, neuron_id: int) -> Path:
        folder = _validate(str(source), "Source")
        if isinstance(neuron_id, bool) or not 0 < int(neuron_id) <= MAX_CELL_ID:
            raise NameError_(f"{neuron_id!r} is not a structure ID")
        path = (self.base / folder / f"c{int(neuron_id)}.json").resolve()
        if self.base not in path.parents:
            raise NameError_("cell render path resolves outside its directory")
        return path

    def get(self, source: str, neuron_id: int) -> dict | None:
        try:
            path = self._path(source, neuron_id)
        except NameError_:
            return None  # not a structure ID, so nothing is attached to it
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def set(self, source: str, neuron_id: int, render: dict) -> None:
        body = _dumps(render)
        if len(body) > MAX_RENDER_BYTES:
            raise QuotaError("attached render exceeds the size of any real "
                             "render option set")
        path = self._path(source, neuron_id)
        if not path.is_file() and len(list(self.base.rglob("c*.json"))) >= MAX_ATTACHED_RENDERS:
            raise QuotaError(f"At most {MAX_ATTACHED_RENDERS} cells can have a render attached")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, body)

    def delete(self, source: str, neuron_id: int) -> bool:
        try:
            path = self._path(source, neuron_id)
        except NameError_:
            return False
        if not path.is_file():
            return False
        path.unlink()
        return True


#: A library document (a material or a gradient) is a handful of numbers.
MAX_LIBRARY_BYTES = 16 * 1024
MAX_LIBRARY_DOCS = 500


class LibraryStore:
    """Named JSON documents shared by every user, one flat confined directory.

    Backs the material and gradient libraries: anything one person saves is
    visible to everyone, which is what a shared lab tool wants for "the felt
    look" or "the strata gradient we use in figures".  Names follow the save
    rules, so nothing can escape ``base``; documents carry who saved them and
    when, filled in by the route.
    """

    def __init__(self, base: str | os.PathLike[str]):
        self.base = Path(base).resolve()

    def ensure_base(self) -> None:
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        stem = _validate(name.removesuffix(".json"), "Library entry")
        path = (self.base / f"{stem}.json").resolve()
        if path.parent != self.base:
            raise NameError_("library entry resolves outside its directory")
        return path

    def list(self) -> list[dict]:
        if not self.base.is_dir():
            return []
        docs = []
        for path in sorted(self.base.glob("*.json"), key=lambda p: p.name.lower()):
            try:
                with path.open("r", encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(doc, dict):
                docs.append(doc)
        return docs

    def get(self, name: str) -> dict | None:
        path = self._path(name)
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        return doc if isinstance(doc, dict) else None

    def set(self, name: str, doc: dict) -> dict:
        path = self._path(name)
        doc = {**doc, "name": path.stem}
        body = _dumps(doc)
        if len(body) > MAX_LIBRARY_BYTES:
            raise QuotaError("library entry is larger than any real material or gradient")
        self.base.mkdir(parents=True, exist_ok=True)
        if not path.exists() and len(list(self.base.glob("*.json"))) >= MAX_LIBRARY_DOCS:
            raise QuotaError(f"the library holds at most {MAX_LIBRARY_DOCS} entries")
        _write_atomic(path, body)
        return doc

    def delete(self, name: str) -> bool:
        path = self._path(name)
        if not path.is_file():
            return False
        path.unlink()
        return True

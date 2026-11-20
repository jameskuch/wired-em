"""Viking OData client.

Ports ``+sbfsem/+io/OData.m`` and the free functions in ``util/import/``
(``readOData``, ``countOData``, ``getODataURL``, ``getODataOptions``,
``getODataScale``, ``queryLabels``, ``getContributions``,
``getLastModifiedAnnotation``, ``getGeometry``).

Deviations from the MATLAB original (see DEVIATIONS.md):

* **Pagination.**  Nothing in the MATLAB code followed ``@odata.nextLink``, so
  any collection larger than the server page size was silently truncated.
  :meth:`ODataClient.get_collection` follows every page.
* ``OData.baseURL`` was built as ``server + 'OData/'`` with no volume segment,
  so ``countCellsInVolume`` and ``getURL`` queried a path that does not exist
  (F029).  The service root now always includes the volume.
* ``getLinkedIDs`` joined its query option with ``&`` where ``?`` was required
  (F028).  Fixed.
* Labels are escaped before interpolation into ``$filter`` (F027): the
  apostrophe doubled, and ``%``, ``&``, ``#``, ``+`` and ``;`` percent-encoded
  so a label cannot end its own value in the query string.
* ``getUsernames``/``getContributions`` concatenated variable-length char rows
  (F030); Python returns a list of trimmed strings.

**Which answers may be reused** (2026-11-20; the audit is
``docs/context/viking-freshness-audit-2026-11-20.md``).  Every query method
has one of two policies, named in :data:`QUERY_POLICIES` and checked by
``tests/unit/test_odata_policies.py``:

* ``live``: asked of Viking every time (``max_age=``:data:`LIVE`).  Links
  between structures, other structures' labels and types, lists that grow,
  provenance, "the last ...".  Nothing Viking can report proves an earlier
  answer still holds: a StructureLink touches neither structure's row, and a
  label belongs to a cell nobody loaded.  The disk copy is used only when Viking
  cannot answer, and :meth:`ODataClient.answers_from_before` lets a caller say
  so.  Inferior c51578's partners stayed unlabeled and five of its synapses
  "unlinked" for a day because these were read once and kept (2026-11-20).
* ``checked``: a cell's own rows (its Locations, LocationLinks, child
  structures and their Locations and links, its closed curves).  Kept on disk;
  a caller that serves them checks them against Viking first (the web service's
  freshness check), or asks again inside :meth:`ODataClient.fresh`.

Ports kept for the library and reached only from tests (audit 2026-10-15,
W10 F23): ``get_url``, ``count_cells_in_volume``, ``count_locations_in_volume``,
``annotations_by_section``, ``get_linked_ids``, ``get_usernames``,
``get_annotation_info``, ``get_user_last_annotations``,
``get_user_last_structures``, ``get_geometry_types`` and ``get_contributions``;
so is :class:`~sbfsem.io.structure_group.ConeMosaic`.  ``get_contributions``
and ``get_annotation_info`` ask once per structure, as the MATLAB did: use
:meth:`ODataClient.fetch_location_provenance_by_parents` (the annotators
report's query) for a population.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import urllib.parse
from collections.abc import Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any, NamedTuple

import httpx

from ..core.volumes import Volumes
from .cache import DiskCache

__all__ = ["LIVE", "QUERY_POLICIES", "ODataClient", "ODataError", "OldAnswer", "odata_url",
           "odata_utc", "parse_datetime"]

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0

#: ``max_age`` of a question asked every time: no stored answer is young enough.
LIVE = 0.0

#: Every query method of :class:`ODataClient` and whether its answer may be
#: reused (see the module docstring).  ``fixed`` is the volume's scale, which
#: Viking does not change.  A method missing here fails
#: ``tests/unit/test_odata_policies.py``.
QUERY_POLICIES: dict[str, str] = {
    # live: asked every time; the disk copy only when Viking cannot answer
    "ids_by_label": "live", "resolve_label": "live", "query_labels": "live",
    "structures_by_type": "live", "annotations_by_section": "live",
    "get_label": "live", "get_labels": "live", "get_structures_brief": "live",
    "get_linked_ids": "live", "get_location": "live", "get_usernames": "live",
    "get_annotation_info": "live", "get_last_annotations": "live",
    "get_last_modified_annotation": "live", "get_user_last_annotations": "live",
    "get_user_last_structures": "live", "get_geometry_types": "live",
    "fetch_location_provenance": "live", "fetch_location_provenance_by_parents": "live",
    "fetch_child_provenance": "live", "get_contributions": "live",
    "fetch_structure_links": "live", "fetch_links_by_parent_structures": "live",
    "location_link_signature": "live", "count_rewrites": "live",
    "structure_freshness": "live", "count": "live", "count_cells_in_volume": "live",
    "count_locations_in_volume": "live",
    # checked: a cell's own rows, kept and checked against Viking before use
    "get_structure": "checked", "fetch_locations": "checked",
    "fetch_location_links": "checked", "fetch_children": "checked",
    "fetch_closed_curves": "checked", "fetch_locations_by_parents": "checked",
    "fetch_links_by_parents": "checked",
    # fixed: does not change
    "get_scale": "fixed", "scale_answer": "fixed",
}


class OldAnswer(NamedTuple):
    """A live question answered from the disk because Viking could not be asked:
    the URL, and when that answer was fetched (POSIX seconds; ``None`` if unknown).
    A named tuple, so a table's ``attrs`` that carry it copy as pandas copies them."""

    url: str
    stored_at: float | None


class ODataError(RuntimeError):
    """Raised when a Viking query fails or returns something unusable.

    ``status`` is the HTTP status Viking (or a gateway in front of it) answered
    with, ``None`` when there was none: a connection that failed, a body that
    was not JSON.  A caller that must tell "no such structure" (404) from "the
    gateway gave up" (502, 504) reads it here; the message names it too.
    """

    def __init__(self, message: str = "", *, status: int | None = None):
        super().__init__(message)
        self.status = status


class _TwoMoments(Exception):
    """A paged list would have been assembled from an older answer and a newer one."""


def _failed(url: str, exc: httpx.HTTPError) -> ODataError:
    """The :class:`ODataError` for a failed request, carrying the status it had."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    where = f" (HTTP {status})" if status is not None else ""
    return ODataError(f"OData request failed{where}: {url}", status=status)


#: What a label may not carry into a query string unescaped: ``%`` first, since
#: it starts every escape, then the characters that end or split a value.  The
#: apostrophe is doubled, not escaped, and a space or a letter stays as it is,
#: so the URL (and the disk cache's key) of an ordinary label is unchanged.
_URL_ESCAPES = {ord("%"): "%25", ord("&"): "%26", ord("#"): "%23",
                ord("+"): "%2B", ord(";"): "%3B"}


def _quote(value: str) -> str:
    """Escape a string literal for an OData ``$filter`` clause in a URL.

    An apostrophe is doubled (OData's escape); ``%``, ``&``, ``#``, ``+`` and
    ``;`` are percent-encoded, because left alone they end or rewrite the
    value in the query string (``Label eq 'R&D'`` searched for ``R``).
    """
    return str(value).replace("'", "''").translate(_URL_ESCAPES)


def parse_datetime(value: str | None) -> datetime | None:
    """Parse Viking's ``2018-02-26T02:09:26.757-07:00`` timestamps.

    Ports ``util/import/parseDateTime.m``.  Returns the datetime with the zone
    the text names (aware for Viking's ``-07:00`` and ``Z``; naive if the text
    names none), or ``None`` for missing or unreadable values.  The zone is
    Viking's label, not the truth: use :func:`odata_utc` for the instant.
    """
    if not value:
        return None
    text = str(value).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        pass
    # Viking occasionally emits more than 6 fractional digits, which
    # ``fromisoformat`` rejects; truncate the fraction and retry.
    match = re.match(r"^(.*?\.)(\d+)(.*)$", text)
    if match:
        head, digits, rest = match.groups()
        try:
            return datetime.fromisoformat(f"{head}{digits[:6]:<06s}{rest}")
        except ValueError:
            return None
    return None


def odata_utc(value) -> datetime | None:
    """A Viking ``LastModified`` or ``Created`` as the instant it names, in UTC.

    Viking writes UTC wall-clock digits.  Its OData labeled them Pacific
    (``-07:00``, ``-08:00`` in winter) and now labels them ``Z``: c19698's
    newest location reads ``05:31:15.223-07:00`` in a row cached before the
    change and ``05:31:15.223Z`` live (2026-10-15).  Read with its label, a
    row cached under the old one is 7 h later than a live one, so an edit
    made within 7 h of a fetch went unnoticed.  The label is dropped and the
    digits read as UTC.
    """
    stamp = value if isinstance(value, datetime) else parse_datetime(value)
    return None if stamp is None else stamp.replace(tzinfo=UTC)


def _instant(stamp: datetime) -> str:
    """A timestamp as an OData literal, in UTC to the second."""
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def odata_url(structure_id: int | None, source, url_type: str = "") -> str:
    """Build a Viking OData endpoint.

    Ports ``util/import/getODataURL.m``.  ``url_type`` is one of ``neuron``,
    ``location``, ``link``, ``child``, ``scale`` or ``''`` for the service root.
    """
    base = Volumes.from_str(source).service_root
    if url_type == "neuron":
        return f"{base}Structures({structure_id})/"
    if url_type == "location":
        return f"{base}Structures({structure_id})/Locations/"
    if url_type == "link":
        # Created (2026-11-20): what the link signature of the copy is read from.
        return f"{base}Structures({structure_id})/LocationLinks/?$select=A,B,Created"
    if url_type == "child":
        return (
            f"{base}Structures({structure_id})/Children"
            # LastModified (2026-11-20): a synapse retagged in Viking is seen.
            "?$select=ID,TypeID,Tags,ParentID,Label,LastModified"
        )
    if url_type == "scale":
        return f"{base}Scale"
    return base


class ODataClient:
    """Thin, paginating, caching wrapper over the Viking OData service."""

    def __init__(
        self,
        source,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        cache: DiskCache | None = None,
        client: httpx.Client | None = None,
        offline: bool = False,
    ):
        self.volume = Volumes.from_str(source)
        self.source = self.volume.source
        self.timeout = timeout
        self.cache = cache if cache is not None else DiskCache()
        self.offline = offline
        self._client = client
        self._owns_client = client is None
        # Per-thread: the service shares one client per volume across request
        # threads, so a bypass must not leak into another user's load.
        self._local = threading.local()

    @contextlib.contextmanager
    def fresh(self) -> Iterator[None]:
        """Skip the disk cache for the reads inside; what comes back is stored.

        The cache has no expiry (an annotation that was fetched once is served
        forever), which is right for a rebuild and wrong for "update this cell
        from Viking" or "has this cell changed": those two run in here.
        """
        before = getattr(self._local, "fresh", False)
        self._local.fresh = True
        try:
            yield
        finally:
            self._local.fresh = before

    @property
    def bypassing_cache(self) -> bool:
        return bool(getattr(self._local, "fresh", False))

    def _ask(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One request to Viking, asked once more on a fresh connection when the
        connection fails (not when it times out, and not for an HTTP status).

        Every query here only reads, so asking again changes nothing in Viking.
        A pooled keep-alive connection the server has closed while it sat idle
        fails on its next use: on production, the first report after a deploy's
        restore lost both its StructureLinks requests to ``[SSL:
        UNEXPECTED_EOF_WHILE_READING]`` and was answered from the disk
        (2026-11-20).  Such failures went unseen while those answers were read
        from the disk for good.  The broken connection leaves the pool, so the
        second try opens a new one.
        """
        for attempt in (1, 2):
            try:
                # .get / .post, as before: a client given in is held to those two.
                response = getattr(self.client, method.lower())(url, **kwargs)
                response.raise_for_status()
                return response
            except httpx.TimeoutException:
                raise
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise
                log.info("%s %s: %s; asked again on a new connection", method, url, exc)
        raise AssertionError("unreachable")  # pragma: no cover

    @contextlib.contextmanager
    def answers_from_before(self) -> Iterator[list[OldAnswer]]:
        """Collect the live questions inside that were answered from the disk.

        A ``live`` query (:data:`QUERY_POLICIES`) falls back on the answer it
        stored last time when Viking cannot be asked; the list this yields
        names each one (:class:`OldAnswer`), so a report built from them can
        say it is not current instead of passing for it.  Per thread, nested
        collectors each see their own.
        """
        stack = getattr(self._local, "old", None)
        if stack is None:
            stack = self._local.old = []
        found: list[OldAnswer] = []
        stack.append(found)
        try:
            yield found
        finally:
            stack.pop()

    def _note_old(self, url: str) -> None:
        for found in getattr(self._local, "old", None) or ():
            found.append(OldAnswer(url, self.cache.stored_at(url)))

    # -- lifecycle ---------------------------------------------------------
    @property
    def base_url(self) -> str:
        """Service root, including the volume segment (F029)."""
        return self.volume.service_root

    def get_url(self) -> str:
        return self.base_url

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"Accept": "application/json"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> ODataClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- primitives --------------------------------------------------------
    # The lists that grow (the structures that carry a label, the vessels, the
    # cones) were trusted for an hour (``LIST_MAX_AGE``, 2026-10 to 2026-11-20):
    # a cell labeled in Viking was not found for up to an hour.  They are
    # ``live`` now (:data:`QUERY_POLICIES`); the whole labeled list of a volume
    # is 85 KB and 0.13 s (Inferior, 2026-11-20).

    def get(self, url: str, *, use_cache: bool = True,
            max_age: float | None = None) -> dict[str, Any]:
        """GET one OData document, honouring the disk cache.

        ``max_age`` (seconds) distrusts a cached answer older than that and
        asks Viking again; :data:`LIVE` (0) asks every time.  If Viking cannot
        be reached the old answer is returned rather than nothing (and named
        to :meth:`answers_from_before`), and an offline client always answers
        from what it holds.
        """
        return self._get(url, use_cache, max_age, False)[0]

    def _get(self, url: str, use_cache: bool, max_age: float | None,
             refresh: bool, *, store: bool = True) -> tuple[dict[str, Any], str]:
        """``(document, how)``; ``refresh`` skips a young entry too.

        ``how`` is ``"cache"`` (a cached answer young enough), ``"old"`` (an
        older cached answer, because Viking could not be asked or the client is
        offline) or ``"live"`` (asked now, and stored unless ``store`` is
        false: a caller assembling a list stores its pages together).
        """
        old = None
        if use_cache and not self.bypassing_cache:
            # A live question (max_age 0) never takes a stored answer as young enough.
            if not refresh and not (max_age is not None and max_age <= 0):
                hit = self.cache.get(url, max_age=max_age)
                if hit is not None:
                    return hit, "cache"
            if max_age is not None:
                old = self.cache.get(url)  # whatever age it is
        if self.offline:
            if old is not None:
                self._note_old(url)
                return old, "old"
            raise ODataError(f"Offline mode: {url} is not cached")
        try:
            response = self._ask("GET", url)
            data = response.json()
        except httpx.HTTPError as exc:
            if old is not None:
                log.warning("%s not refreshed (%s); using the older answer", url, exc)
                self._note_old(url)
                return old, "old"
            raise _failed(url, exc) from exc
        except ValueError as exc:
            if old is not None:
                log.warning("%s not refreshed (not JSON); using the older answer", url)
                self._note_old(url)
                return old, "old"
            raise ODataError(f"OData response was not JSON: {url}") from exc
        if use_cache and store:
            self.cache.set(url, data)
        return data, "live"

    def get_collection(self, url: str, *, use_cache: bool = True,
                       max_age: float | None = None) -> list[dict[str, Any]]:
        """GET a collection, following every ``@odata.nextLink`` page.

        The MATLAB client read only the first page, so any query returning more
        rows than the server page size was silently truncated.

        With ``max_age`` a list is never assembled from two moments: once one
        page had to be asked again the pages after it are too, and the pages are
        stored together once the whole list is in.  If a page then cannot be had
        (or, the other way round, the pages before it were the older answer and
        this one is live), the walk is given up and the older answer is
        returned whole, from entries nothing has overwritten.
        """
        if max_age is None or not use_cache or self.bypassing_cache:
            return self._collect(url, use_cache, max_age, store=True)[0]
        try:
            rows, staged = self._collect(url, use_cache, max_age, store=False, one_moment=True)
        except _TwoMoments as why:
            log.warning("%s: %s; using the older answer whole", url, why)
            self._note_old(url)
            return self._collect(url, use_cache, None, store=False)[0]
        for page_url, payload in staged:
            self.cache.set(page_url, payload)
        return rows

    def _collect(self, url: str, use_cache: bool, max_age: float | None, *, store: bool,
                 one_moment: bool = False) -> tuple[list[dict[str, Any]], list[tuple[str, dict]]]:
        """``(rows, the pages fetched live and not yet stored)`` of a paged collection."""
        rows: list[dict[str, Any]] = []
        staged: list[tuple[str, dict[str, Any]]] = []
        seen: set[str] = set()
        hows: set[str] = set()
        next_url: str | None = url
        refresh = False
        while next_url:
            if next_url in seen:  # defensive: a server loop would hang forever
                log.warning("OData pagination loop at %s", next_url)
                break
            seen.add(next_url)
            # A page that cannot be had at all (no older answer to fall back on either)
            # fails the walk, as it always did.
            payload, how = self._get(next_url, use_cache, max_age, refresh, store=store)
            hows.add(how)
            if one_moment and "live" in hows and "old" in hows:
                raise _TwoMoments(f"page {len(seen)} is {how} beside pages that are not")
            if how == "live" and not store:
                staged.append((next_url, payload))
            refresh = refresh or (max_age is not None and how == "live")
            value = payload.get("value")
            if value is None:
                # A single entity, not a collection.
                return [payload], staged
            rows.extend(value)
            next_url = payload.get("@odata.nextLink") or payload.get("odata.nextLink")
        return rows, staged

    def count_rewrites(self, start: datetime, end: datetime, created_before: datetime) -> int:
        """How many annotations anywhere in the volume were last written between
        ``start`` and ``end`` (inclusive) and created before ``created_before``.

        Always asked live: a count over a past window still falls as its rows
        are written again.  Answered by the ``/$count`` segment, which is plain
        text behind a byte-order mark, not a page of rows.
        """
        url = (
            f"{self.base_url}Locations/$count?$filter="
            f"LastModified ge {_instant(start)} and LastModified le {_instant(end)}"
            f" and Created lt {_instant(created_before)}"
        )
        if self.offline:
            raise ODataError(f"Offline mode: {url} is not cached")
        try:
            response = self._ask("GET", url)
            return int(response.text.lstrip("\ufeff").strip())
        except httpx.HTTPError as exc:
            raise _failed(url, exc) from exc
        except ValueError as exc:
            raise ODataError(f"OData count was not a number: {url}") from exc

    #: IDs per ``StructureFreshnessBatch`` request.  500 answered in 0.35 s
    #: on RC2 (2026-10-15); a session restore is at most a few hundred cells.
    FRESHNESS_CHUNK = 500

    def structure_freshness(
        self, structure_ids: Iterable[int], *, depth: int = 0,
    ) -> dict[int, tuple[datetime, int]]:
        """``{ID: (LastModified, AnnotationCount)}`` from Viking's ``StructureFreshnessBatch``.

        Always live, never the disk cache, and one request per
        :attr:`FRESHNESS_CHUNK` IDs however many cells.  ``depth`` 0 counts the
        structure's own Locations; 1 adds its direct child structures
        (synapses and the like).  ``LastModified`` is the newest of the
        structures' own rows and those Locations, in UTC (:func:`odata_utc`).
        IDs Viking does not know are left out.  Raises :class:`ODataError` when
        the volume cannot be reached or has no such action (Viking added it on
        2026-10-15).
        """
        ids = sorted({int(i) for i in structure_ids})
        if not ids:
            return {}
        url = f"{self.base_url}StructureFreshnessBatch"
        if self.offline:
            raise ODataError(f"Offline mode: {url} is never cached")
        out: dict[int, tuple[datetime, int]] = {}
        for start in range(0, len(ids), self.FRESHNESS_CHUNK):
            chunk = ids[start:start + self.FRESHNESS_CHUNK]
            try:
                response = self._ask("POST", url, json={"IDs": chunk, "Depth": int(depth)})
                rows = response.json().get("value", [])
            except httpx.HTTPError as exc:
                raise _failed(url, exc) from exc
            except (ValueError, AttributeError) as exc:
                raise ODataError(f"OData response was not a collection: {url}") from exc
            for row in rows:
                stamp = odata_utc(row.get("LastModified"))
                if stamp is not None and "StructureID" in row:
                    out[int(row["StructureID"])] = (stamp, int(row.get("AnnotationCount") or 0))
        return out

    def count(self, url: str) -> int:
        """Ports ``util/import/countOData.m``."""
        joiner = "&" if "?" in url else "?"
        payload = self.get(f"{url}{joiner}$count=true", max_age=LIVE)
        for key in ("@odata.count", "odata.count"):
            if key in payload:
                return int(payload[key])
        return len(payload.get("value", []))

    # -- volume-level queries ---------------------------------------------
    def get_scale(self) -> tuple[float, float, float]:
        """Volume scale in nm, with the Nasal-volume override preserved.

        Ports ``util/import/getODataScale.m``.  Falls back to the cached scale
        when the server is unreachable, as ``viking2micron`` did.
        """
        return self.scale_answer()[0]

    def scale_answer(self) -> tuple[tuple[float, float, float], str]:
        """The volume scale in nm and where it came from: ``"viking"`` (its OData
        ``Scale``, or that answer as cached), ``"stored"`` (the cached scale, by the
        volume's override) or ``"fallback"`` (the cached scale, because Viking did
        not answer or answered something unreadable)."""
        if self.volume.info.force_cached_scale:
            return self.volume.cached_scale, "stored"
        try:
            data = self.get(odata_url(None, self.volume, "scale"))
        except ODataError:
            log.info("Volume scale query failed for %s; using cached scale", self.source)
            return self.volume.cached_scale, "fallback"
        try:
            return (
                float(data["X"]["Value"]),
                float(data["Y"]["Value"]),
                float(data["Z"]["Value"]),
            ), "viking"
        except (KeyError, TypeError, ValueError):
            return self.volume.cached_scale, "fallback"

    def count_cells_in_volume(self) -> int:
        return self.count(f"{self.base_url}Structures?$filter=TypeID eq 1")

    def count_locations_in_volume(self) -> int:
        """The MATLAB method was an empty stub."""
        return self.count(f"{self.base_url}Locations")

    def ids_by_label(self, label: str, exact_match: bool = True) -> list[dict[str, Any]]:
        """Structure IDs matching a label.

        Returns a list of ``{'ID': ..., 'Label': ...}``.  The MATLAB version
        returned an unassigned output when nothing matched (F026) and did not
        escape the label (F027).
        """
        if exact_match:
            url = (
                f"{self.base_url}Structures?$filter=Label eq '{_quote(label)}'"
                "&$select=ID,Label"
            )
        else:
            url = (
                f"{self.base_url}Structures?$filter=contains(Label,'{_quote(label)}')"
                "&$select=ID,Label"
            )
        return self.get_collection(url, max_age=LIVE)

    def resolve_label(self, term: str) -> list[dict[str, Any]]:
        """Parent structures whose label contains ``term``, case-insensitively.

        The exact filter the Viking Data Export portal uses (jamesra/Viking,
        ``Jotunn`` branch): a case-insensitive substring test via
        ``contains(tolower(...))`` — OData has no regular expressions — and
        ``ParentID eq null`` because only parent structures are neurons; their
        children are the individual annotations.  Pagination follows every
        ``@odata.nextLink`` continuation, as everything here does.
        """
        url = (
            f"{self.base_url}Structures"
            f"?$filter=contains(tolower(Label),'{_quote(term.lower())}')"
            " and ParentID eq null&$select=ID,Label&$orderby=ID"
        )
        return self.get_collection(url, max_age=LIVE)

    def query_labels(self, search: str | None = None) -> list[dict[str, Any]]:
        """Every labeled structure in the volume.

        Ports ``util/import/queryLabels.m``, which used default ``webread``
        options instead of the OData JSON decoder (F067).
        """
        url = f"{self.base_url}Structures?$select=ID,Label,TypeID&$filter=Label ne null"
        rows = self.get_collection(url, max_age=LIVE)
        if search:
            needle = search.lower()
            rows = [r for r in rows if needle in str(r.get("Label", "")).lower()]
        return rows

    def annotations_by_section(self, sections: Iterable[int]) -> list[dict[str, Any]]:
        z = list(sections)
        url = (
            f"{self.base_url}Locations?$filter=Z le {max(z)} and Z ge {min(z)}"
            " and TypeCode eq 1&$select=ID,ParentID,X,Y,Z,Radius"
        )
        return self.get_collection(url, max_age=LIVE)

    def get_label(self, structure_id: int) -> str:
        data = self.get(f"{self.base_url}Structures({structure_id})?$select=Label", max_age=LIVE)
        return str(data.get("Label") or "").strip()

    def get_labels(self, structure_ids: Iterable[int]) -> dict[int, str]:
        """Labels for many structures in one batched query.

        ``getAllLinkedNeurons`` called ``getLabel`` once per partner neuron,
        which dominated the runtime of building a connectivity network.
        """
        rows = self._id_filter_query(
            "{base}Structures?$filter={filter}&$select=ID,Label",
            structure_ids,
            field="ID",
            max_age=LIVE,
        )
        return {
            int(r["ID"]): str(r.get("Label") or "").strip()
            for r in rows
            if r.get("ID") is not None
        }

    def get_structure(self, structure_id: int) -> dict[str, Any]:
        """Fetch one Structure entity."""
        return self.get(odata_url(structure_id, self.volume, "neuron"))

    def get_linked_ids(self, location_id: int) -> list[dict[str, Any]]:
        """Location links for one annotation.

        The MATLAB version joined ``$expand`` with ``&`` where ``?`` was
        required, producing a malformed URL (F028).
        """
        url = f"{self.base_url}Locations({location_id})?$expand=LocationLinksA,LocationLinksB"
        return self.get_collection(url, max_age=LIVE)

    def get_location(self, location_id: int) -> dict[str, Any]:
        """One Location row as Viking holds it now (where Goto Location goes)."""
        return self.get(f"{self.base_url}Locations({int(location_id)})", max_age=LIVE)

    def location_link_signature(self, structure_id: int) -> tuple[int, datetime | None]:
        """``(how many LocationLinks, the newest one's Created)`` of one structure.

        What Viking's ``StructureFreshness`` cannot see: a link made or removed
        between two annotations that already exist touches no Location, so
        neither the newest edit nor the count moves (Inferior c51578: 369 of 571
        links are newer than both their annotations; c211402's newest link is
        3 ms newer than the stamp Viking gives, measured 2026-11-20).  The
        count sees a link removed, the newest date one made, so the pair sees
        any change.  One request, 0.12 s.  A link joins two annotations of one
        structure, so its ``A`` end names the structure.
        """
        url = (
            f"{self.base_url}LocationLinks?$filter=LocationA/ParentID eq {int(structure_id)}"
            "&$orderby=Created desc&$top=1&$count=true&$select=A,B,Created"
        )
        if self.offline:
            raise ODataError(f"Offline mode: {url} is never cached")
        try:
            response = self._ask("GET", url)
            payload = response.json()
        except httpx.HTTPError as exc:
            raise _failed(url, exc) from exc
        except ValueError as exc:
            raise ODataError(f"OData response was not JSON: {url}") from exc
        count = payload.get("@odata.count", payload.get("odata.count"))
        if count is None:
            raise ODataError(f"OData answered no count: {url}")
        rows = payload.get("value") or []
        newest = odata_utc(rows[0].get("Created")) if rows else None
        return int(count), newest

    def get_usernames(self, structure_id: int) -> tuple[list[str], list[int]]:
        """Annotating usernames and their location IDs for a structure."""
        url = f"{self.base_url}Structures({structure_id})/Locations?$select=Username,ID"
        rows = self.get_collection(url, max_age=LIVE)
        return (
            [str(r.get("Username") or "").strip() for r in rows],
            [int(r["ID"]) for r in rows],
        )

    def get_annotation_info(self, ids: Iterable[int]) -> list[dict[str, Any]]:
        """Last-modified date and username for each structure ID."""
        out = []
        for sid in ids:
            data = self.get(
                f"{self.base_url}Structures({sid})?$select=Username,LastModified", max_age=LIVE
            )
            out.append(
                {
                    "ID": int(sid),
                    "Username": str(data.get("Username") or "").strip(),
                    "LastModified": odata_utc(data.get("LastModified")),
                }
            )
        return out

    def get_last_annotations(self, structure_id: int, n: int = 1) -> list[dict[str, Any]]:
        url = (
            f"{self.base_url}Structures({structure_id})/Locations"
            f"?$orderby=LastModified desc&$top={int(n)}"
        )
        return self.get_collection(url, max_age=LIVE)

    def get_last_modified_annotation(self, structure_id: int) -> dict[str, Any] | None:
        """Ports ``util/import/getLastModifiedAnnotation.m`` (F068: its
        ``nargin < 3`` guard could never fire in a two-argument function)."""
        rows = self.get_last_annotations(structure_id, 1)
        return rows[0] if rows else None

    def get_user_last_annotations(self, username: str, n: int = 1) -> list[dict[str, Any]]:
        url = (
            f"{self.base_url}Locations?$filter=Username eq '{_quote(username)}'"
            f"&$orderby=LastModified desc&$select=LastModified,ID,ParentID,TypeCode"
            f"&$top={int(n)}"
        )
        return self.get_collection(url, max_age=LIVE)

    def get_user_last_structures(
        self, username: str, n: int | None = None
    ) -> list[dict[str, Any]]:
        url = (
            f"{self.base_url}Structures?$filter=Username eq '{_quote(username)}'"
            "&$select=ID,TypeID&$orderby=LastModified desc"
        )
        if n is not None:
            url += f"&$top={int(n)}"
        return self.get_collection(url, max_age=LIVE)

    def get_geometry_types(self, structure_id: int) -> list[int]:
        """Distinct ``TypeCode`` values used by a structure's annotations.

        Ports ``util/import/getGeometry.m``, which applied ``struct2array`` to a
        cell array (F065) and bypassed ``getServiceRoot``, mis-handling the Nasal
        volume's URL alias (F066).
        """
        rows = self.get_collection(
            f"{self.base_url}Structures({structure_id})/Locations?$select=TypeCode",
            max_age=LIVE,
        )
        return sorted({int(r["TypeCode"]) for r in rows if r.get("TypeCode") is not None})

    #: The provenance columns of an annotation: who last wrote it, when it was
    #: first placed and when it was last written.  Selected explicitly because
    #: a bare ``Locations`` row carries the two geometry blobs as well, which
    #: are three orders of magnitude larger and of no use here.
    PROVENANCE_SELECT = "ID,ParentID,Username,Created,LastModified,TypeCode,Z"

    def fetch_location_provenance(self, structure_id: int) -> list[dict[str, Any]]:
        """Who wrote each of one structure's annotations, and when."""
        return self.get_collection(
            f"{self.base_url}Structures({int(structure_id)})/Locations"
            f"?$select={self.PROVENANCE_SELECT}",
            max_age=LIVE,
        )

    def fetch_location_provenance_by_parents(
        self, parent_ids: Iterable[int], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        """The same columns for a set of structures, batched.

        A cell's synapses are child structures of their own, so the annotators
        of a cell are the annotators of the cell's own annotations *and* of its
        children's.
        """
        return self._id_filter_query(
            "{base}Locations?$filter={filter}&$select=" + self.PROVENANCE_SELECT,
            parent_ids,
            batch_size=batch_size,
            max_age=LIVE,
        )

    def fetch_child_provenance(self, structure_id: int) -> list[dict[str, Any]]:
        """One row per child structure: who created it, and when.

        ``fetch_children`` selects what a synapse needs to render;
        ``Username`` is not in it, and adding it there would change the
        cached shape of every loaded cell.
        """
        return self.get_collection(
            f"{self.base_url}Structures"
            f"?$filter=ParentID eq {int(structure_id)}"
            "&$select=ID,TypeID,Label,Username,Created,LastModified",
            max_age=LIVE,
        )

    def get_contributions(self, structure_ids: Iterable[int]) -> dict[str, int]:
        """Annotation counts per username across the given structures."""
        counts: dict[str, int] = {}
        for sid in structure_ids:
            names, _ = self.get_usernames(int(sid))
            for name in names:
                counts[name] = counts.get(name, 0) + 1
        return counts

    def structures_by_type(self, type_id: int) -> list[dict[str, Any]]:
        """Every structure of one Viking type.  Ports ``StructureGroup.queryByTypeID``."""
        url = f"{self.base_url}Structures?$filter=TypeID eq {int(type_id)}&$select=ID,ParentID"
        return self.get_collection(url, max_age=LIVE)

    # -- structure-level queries ------------------------------------------
    def fetch_locations(self, structure_id: int) -> list[dict[str, Any]]:
        return self.get_collection(odata_url(structure_id, self.volume, "location"))

    def fetch_location_links(self, structure_id: int) -> list[dict[str, Any]]:
        return self.get_collection(odata_url(structure_id, self.volume, "link"))

    def fetch_children(self, structure_id: int) -> list[dict[str, Any]]:
        return self.get_collection(odata_url(structure_id, self.volume, "child"))

    def fetch_closed_curves(self, structure_id: int) -> list[dict[str, Any]]:
        url = (
            f"{self.base_url}Structures({structure_id})/Locations"
            "?$filter=TypeCode eq 6"
        )
        return self.get_collection(url)

    # -- batched queries (replaces the MATLAB N+1 loops) -------------------
    #: Viking accepts ``in (...)`` lists of at least 200 IDs but rejects an
    #: ``or`` chain of about 22 clauses with a 400, and a very long URL with a
    #: 404.  Both limits are probed empirically, so the batchers degrade
    #: gracefully rather than assuming either shape works.
    IN_BATCH_SIZE = 100
    OR_BATCH_SIZE = 20

    #: Batches of one query asked of Viking at once.  c51578's link report (208
    #: synapses) took 2.1 s one request after another from the server
    #: (2026-11-20); every answer is live now, so the waits are worth sharing.
    BATCH_WORKERS = 4

    def _carry(self, fn):
        """``fn`` as it would run on this thread: inside :meth:`fresh` if this
        thread is, and naming its old answers to this thread's collectors."""
        fresh = self.bypassing_cache
        collectors = list(getattr(self._local, "old", None) or ())

        def run(*args, **kwargs):
            before = (getattr(self._local, "fresh", False), getattr(self._local, "old", None))
            self._local.fresh, self._local.old = fresh, collectors
            try:
                return fn(*args, **kwargs)
            finally:
                self._local.fresh, self._local.old = before

        return run

    def _in_parallel(self, fn, items: list) -> list:
        """``[fn(item) for item in items]``, up to :attr:`BATCH_WORKERS` at once, in order."""
        if len(items) <= 1 or self.BATCH_WORKERS <= 1:
            return [fn(item) for item in items]
        with ThreadPoolExecutor(max_workers=min(self.BATCH_WORKERS, len(items))) as pool:
            return list(pool.map(self._carry(fn), items))

    def _id_filter_query(
        self, template: str, ids: Iterable[int], *, field: str = "ParentID",
        batch_size: int | None = None, max_age: float | None = None,
    ) -> list[dict[str, Any]]:
        """Run ``template`` once per batch of IDs, shrinking the batch on refusal.

        ``template`` is a format string with a ``{filter}`` placeholder.  The
        first attempt uses OData's ``in`` operator; if the server rejects it the
        query falls back to an ``or`` chain, and finally to one request per ID,
        so a deployment that supports none of the compact forms still works.
        The batches are asked side by side (:attr:`BATCH_WORKERS`); ``max_age``
        is :meth:`get`'s.
        """
        ids = [int(i) for i in ids]
        if not ids:
            return []
        chunks = list(_chunks(ids, batch_size or self.IN_BATCH_SIZE))
        rows: list[dict[str, Any]] = []
        for part in self._in_parallel(
                lambda chunk: self._filter_chunk(template, field, chunk, max_age), chunks):
            rows.extend(part)
        return rows

    def _filter_chunk(self, template: str, field: str, chunk: list[int],
                      max_age: float | None = None) -> list[dict[str, Any]]:
        clause = f"{field} in ({','.join(str(i) for i in chunk)})"
        try:
            return self.get_collection(self._build(template, clause), max_age=max_age)
        except ODataError:
            log.debug("`in` filter refused for %u ids; falling back to `or`", len(chunk))

        rows: list[dict[str, Any]] = []
        for group in _chunks(chunk, self.OR_BATCH_SIZE):
            clause = " or ".join(f"{field} eq {i}" for i in group)
            try:
                rows.extend(self.get_collection(self._build(template, clause), max_age=max_age))
                continue
            except ODataError:
                log.debug("`or` filter refused for %u ids; falling back to one query per id",
                          len(group))
            for single in group:
                rows.extend(
                    self.get_collection(self._build(template, f"{field} eq {single}"),
                                        max_age=max_age)
                )
        return rows

    def _build(self, template: str, clause: str) -> str:
        return template.format(
            base=self.base_url,
            filter=urllib.parse.quote(clause, safe=" (),/"),
        )

    def fetch_locations_by_parents(
        self, parent_ids: Iterable[int], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        """All annotations belonging to any of ``parent_ids``.

        ``SynapseOData.expandChildData`` issued one request per child structure —
        hundreds per neuron.  Batching them is the single largest performance win
        available and changes no result.
        """
        return self._id_filter_query(
            "{base}Locations?$filter={filter}", parent_ids, batch_size=batch_size
        )

    def fetch_links_by_parents(
        self, parent_ids: Iterable[int], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        """Location links for a set of structures, batched.

        ``LocationLinks`` carries no ParentID, so this expands through
        ``Locations`` and flattens the result into ``(ID, A, B)`` edge rows.
        """
        rows = self._id_filter_query(
            "{base}Locations?$filter={filter}"
            "&$select=ID,ParentID&$expand=LocationLinksA($select=A,B)",
            parent_ids,
            batch_size=batch_size,
        )
        edges: list[dict[str, Any]] = []
        for row in rows:
            parent = row.get("ParentID")
            for link in row.get("LocationLinksA") or []:
                edges.append({"ID": parent, "A": link["A"], "B": link["B"]})
        return edges

    def fetch_structure_links(
        self, structure_ids: Iterable[int], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        """StructureLinks with both endpoints' parents resolved, batched.

        Replaces the per-ID loop in ``util/import/getStructureLinks.m`` and
        ``util/network/getLinkedNeurons.m``.
        """
        expand = (
            "&$expand=Source($select=ID,ParentID,TypeID),"
            "Target($select=ID,ParentID,TypeID)"
        )
        ids = [int(i) for i in structure_ids]
        if not ids:
            return []
        return _unique_links(self._in_parallel(
            lambda field: self._id_filter_query(
                "{base}StructureLinks?$filter={filter}" + expand,
                ids, field=field, batch_size=batch_size, max_age=LIVE,
            ),
            ["SourceID", "TargetID"],
        ))

    def fetch_links_by_parent_structures(
        self, parent_ids: Iterable[int], batch_size: int | None = None
    ) -> list[dict[str, Any]]:
        """Every StructureLink touching a child of any of ``parent_ids``.

        A neuron's whole partner list in two batched requests (one per end of
        the link), with both children's ``ParentID``, ``TypeID`` and ``Tags``
        expanded — enough to name the partner neuron and the synapse type
        without loading anyone's annotations.  Viking's OData service accepts
        navigation-property filters (``Source/ParentID in (…)``; verified on
        websvc.codepharm.net, 2026-09-11), which is what makes multi-hop
        connectivity queries affordable: each hop is two requests, not one
        per neuron.
        """
        expand = (
            "&$expand=Source($select=ID,ParentID,TypeID,Tags),"
            "Target($select=ID,ParentID,TypeID,Tags)"
        )
        ids = [int(i) for i in parent_ids]
        if not ids:
            return []
        return _unique_links(self._in_parallel(
            lambda field: self._id_filter_query(
                "{base}StructureLinks?$filter={filter}" + expand,
                ids, field=field, batch_size=batch_size, max_age=LIVE,
            ),
            ["Source/ParentID", "Target/ParentID"],
        ))

    def get_structures_brief(self, structure_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        """``{ID: {"label", "type_id", "parent_id"}}`` for many structures, batched."""
        rows = self._id_filter_query(
            "{base}Structures?$filter={filter}&$select=ID,Label,TypeID,ParentID",
            structure_ids,
            field="ID",
            max_age=LIVE,
        )
        out: dict[int, dict[str, Any]] = {}
        for r in rows:
            if r.get("ID") is None:
                continue
            out[int(r["ID"])] = {
                "label": str(r.get("Label") or "").strip(),
                "type_id": int(r["TypeID"]) if r.get("TypeID") is not None else None,
                "parent_id": int(r["ParentID"]) if r.get("ParentID") is not None else None,
            }
        return out


def _unique_links(parts: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """The StructureLinks of both ends' queries, each once, in the order first seen."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for part in parts:
        for row in part:
            key = (row.get("SourceID"), row.get("TargetID"))
            if key not in seen:
                seen.add(key)
                rows.append(row)
    return rows


def _chunks(items: list, size: int) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]

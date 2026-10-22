"""HTTP surface over :class:`sbfsem.service.facade.SbfsemService`.

Every route is a thin adapter: it parses parameters, calls one service method and
returns the DTO.  No analysis or geometry logic lives here, which is what keeps
the backend usable without the web layer (and would let a different frontend
attach to the same facade).
"""

from __future__ import annotations

import base64
import binascii
import email.message
import hashlib
import json
import logging
import math
import re
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, cast

from fastapi import Body, Depends, HTTPException, Query, Request, UploadFile
from fastapi import Path as PathParam
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, BeforeValidator, Field, TypeAdapter
from pydantic_core import PydanticSerializationError
from starlette.concurrency import run_in_threadpool

from sbfsem.analysis.correction_checks import DEFAULT_RATIO as PULL_DEFAULT_RATIO
from sbfsem.analysis.correction_checks import MAX_RATIO as PULL_MAX_RATIO
from sbfsem.analysis.correction_checks import MIN_RATIO as PULL_MIN_RATIO
from sbfsem.core.volumes import validate_source
from sbfsem.io.corrections import correction_mode
from sbfsem.io.odata import ODataError
from sbfsem.service.dto import (
    AnalysisResultDTO,
    AnnotationWireDTO,
    AnnotatorsDTO,
    ClosedCurveDTO,
    ConnectivityDTO,
    CorrectionLogDTO,
    CorrectionPullsDTO,
    DendriteDiameterDTO,
    DeviationMapDTO,
    EdgeDTO,
    FreshnessDTO,
    HullDTO,
    IERatioDTO,
    LinkedNeuronsDTO,
    LocationDTO,
    MeshDiffDTO,
    MeshDTO,
    MorphometricsDTO,
    NetworkDTO,
    NeuronDetailDTO,
    NeuronSummaryDTO,
    NodeDTO,
    RegistrationQualityDTO,
    ResolvedSpecDTO,
    SearchResultDTO,
    ShiftSummaryDTO,
    SkeletonCheckDTO,
    SkeletonDTO,
    StratificationDTO,
    SynapsePointDTO,
    VolumeInfoDTO,
)
from sbfsem.service.estimate import FINEST_UM as ESTIMATE_FINEST_UM
from sbfsem.service.facade import (
    MeshBuffers,
    NeuronNotLoadedError,
    ResultBuffers,
    SbfsemService,
    curve_averaging,
    requested_copy,
)
from sbfsem.util.memory import trim_memory

from .. import mesh_binary
from ..json_response import EncodedJSON, SafeJSONResponse, SafeJSONRouter, json_float_parts
from ..library import validate_library_doc
from ..saves import MAX_CELL_ID, CellRenderStore, LibraryStore, QuotaError, SaveError, SaveStore

log = logging.getLogger(__name__)

#: How an IPL depth is measured (``sbfsem.analysis.boundary.IPL_METHODS``).
IplMethod = Literal["column", "normal"]


def _require_volume(request: Request, source: str) -> None:
    """403 unless a signed-in Viking user may open ``source``.

    The one place the refusal is made, shared by the router's guard (which
    reads the source off the path, the query string or a body that names it)
    and by the routes whose body can *omit* it or carry several (``POST
    /neurons`` defaults to Inferior Monkey; the freshness batch names one per
    cell): a guard that reads only what a request says cannot see what the
    route will assume.  Inert unless a Viking session is driving the request.
    """
    session = getattr(request.state, "viking_session", None)
    if session is None or not request.app.state.viking.config.gate_volumes:
        return
    try:
        volume = validate_source(source)
    except (ValueError, TypeError):
        return  # Not a volume we know; the route's own 404 is the right answer.
    if not session.may_read(volume):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Your Viking account has no access to {volume}. "
                "Ask the Viking administrators to share this volume with you."
            ),
        )


#: The methods whose body FastAPI reads for a route that declares one.
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _reads_body_as_json(request: Request) -> bool:
    """Whether FastAPI will take this request's body for JSON.

    Decided as FastAPI decides it (``fastapi.routing.get_request_handler``),
    because a guard that reads a body differently from the route behind it
    sees nothing in the one the route reads: the media type is compared
    case-insensitively after whitespace is stripped, any ``+json`` subtype is
    JSON (``application/vnd.api+json``), and a body with no Content-Type at all
    is JSON to the FastAPI versions that allow it (the current one refuses it;
    reading it here costs nothing, since the route then answers 422).  The
    guard once tested ``startswith("application/json")``, which missed every
    one of those spellings (2026-10-16).
    """
    value = request.headers.get("content-type")
    if not value:
        return True
    message = email.message.Message()
    message["content-type"] = value
    if message.get_content_maintype() != "application":
        return False
    subtype = message.get_content_subtype()
    return subtype == "json" or subtype.endswith("+json")


async def _volumes_named(request: Request) -> list[str]:
    """Every volume a request names, in every place it can: the path, each
    ``source`` of the query string, and the ``source`` of a JSON body.

    All of them, not the first found: a request can carry a granted volume in
    one place and another in a second, and the route reads whichever the guard
    did not (``?source=RC1`` beside a body that names Inferior Monkey).
    """
    named: list[str] = []
    in_path = request.path_params.get("source")
    if in_path is not None:
        named.append(str(in_path))
    named.extend(request.query_params.getlist("source"))
    if request.method in _BODY_METHODS and _reads_body_as_json(request):
        # Starlette caches the parsed body, so reading it here does not stop the
        # endpoint from reading it again (FastAPI has read it already, for a
        # route that declares one).
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):    # empty, or not JSON after all: it names nothing
            body = None
        if isinstance(body, dict) and body.get("source") is not None:
            named.append(str(body["source"]))
    return named


async def _volume_access_guard(request: Request) -> None:
    """Refuse any request naming a volume the signed-in user may not open.

    One router-wide dependency rather than a check in each of the forty-odd
    routes that take a ``source``: a route added later is covered the day it
    is written, and there is no per-route line to forget.  It looks in all
    three places a source can arrive -- the path, the query string, and the
    body of any request FastAPI reads as JSON -- and in *every* one of them
    (:func:`_volumes_named`): missing one is the whole game.

    It can read only what the request says, and only as well as it parses.  A
    body that leaves the source out (``POST /neurons`` then loads Inferior
    Monkey), that names several (``POST /neurons/freshness``) or that is
    parsed by the route only (``POST /neurons/import``), and a body whose
    volume matters enough not to hang on this guard's reading
    (``POST /connectivity/query``), is checked by its route with
    :func:`_require_volume`; ``tests/unit/test_viking.py`` lists every route
    that takes a body and tries each with the spellings of JSON, a second
    source in the query string and a list of cells, so a new one has to be
    put on a side, and passes for the way it is spelled.

    Inert unless a Viking session is driving the request: with the built-in
    login, or with no login at all, every volume stays as open as it was.
    """
    session = getattr(request.state, "viking_session", None)
    if session is None or not request.app.state.viking.config.gate_volumes:
        return
    for named in await _volumes_named(request):
        _require_volume(request, named)


async def _cell_copy(request: Request):
    """Answer the request from the copy of the cell the page loaded.

    The service holds one copy of a cell per setting in use (correction mode,
    current section transforms or not), because settings are per tab.  The
    page names its copy as ``?copy=`` (a summary's ``copy_tag``) on every
    request for the cell; without it, the copy loaded most recently answers.
    Async, so the choice is made in the request's own context, which the
    route then runs in.
    """
    with requested_copy(request.query_params.get("copy")):
        yield


router = SafeJSONRouter(prefix="/api",
                        dependencies=[Depends(_volume_access_guard), Depends(_cell_copy)])

#: FastAPI marker, hoisted so it is not constructed in an argument default.
_BODY = Body(...)

#: A structure ID in a path that names a file (an attached render): Viking
#: numbers them as 32-bit database keys, so anything else is not a cell.
CellId = Annotated[int, PathParam(gt=0, le=MAX_CELL_ID)]

EXPORT_MEDIA_TYPES = {
    "swc": "text/plain",
    "json": "application/json",
    "dae": "model/vnd.collada+xml",
    "stl": "model/stl",
    "obj": "text/plain",
    "glb": "model/gltf-binary",
    "ply": "application/octet-stream",
}


def service(request: Request) -> SbfsemService:
    return request.app.state.service


def _source(value: str) -> str:
    try:
        return validate_source(value)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _neuron_or_404(svc: SbfsemService, neuron_id: int, source: str):
    try:
        return svc.neuron(neuron_id, source)
    except NeuronNotLoadedError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# -- shared libraries: materials and gradients ------------------------------
def _library(request: Request, kind: str) -> LibraryStore:
    stores = getattr(request.app.state, "libraries", {})
    store = stores.get(kind)
    if store is None:
        raise HTTPException(status_code=404, detail=f"No such library: {kind!r}")
    return store


@router.get("/library/{kind}")
def library_list(request: Request, kind: str):
    """Every shared entry of a library (``materials`` or ``gradients``)."""
    return {"kind": kind, "entries": _library(request, kind).list()}


@router.put("/library/{kind}/{name}")
def library_put(request: Request, kind: str, name: str, body: dict = _BODY):
    """Create or replace a shared entry.  Visible to every user at once."""
    store = _library(request, kind)
    try:
        doc = validate_library_doc(kind, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    user = getattr(request.state, "user", None)
    doc["saved_by"] = user if isinstance(user, str) else None  # the request's user is a name or None
    doc["saved_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        return store.set(name, doc)
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/library/{kind}/{name}")
def library_delete(request: Request, kind: str, name: str):
    store = _library(request, kind)
    try:
        return {"removed": store.delete(name)}
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -- volumes ---------------------------------------------------------------
@router.get("/health")
def health(request: Request) -> dict:
    from sbfsem import __version__

    jobs = getattr(request.app.state, "mesh_jobs", None)
    return {"status": "ok", "version": __version__,
            "builds": jobs.load() if jobs is not None else None}


@lru_cache(maxsize=1)
def _meshlab_available() -> bool:
    from sbfsem.geometry import meshlab

    return meshlab.available()


@router.get("/build")
def build(request: Request) -> dict:
    """Identity of the running build, polled by the browser to spot a redeploy.

    Deliberately the cheapest route in the application: every open tab hits it
    on a timer, and it must never contend with a mesh build for the worker.
    """
    from sbfsem import __version__

    state = request.app.state
    return {
        "version": __version__,
        # Whether this server can honor the MeshLab remesh option (the
        # optional, GPL-3 pymeshlab): the app grays the control out when not.
        "meshlab": _meshlab_available(),
        "assets": getattr(state, "assets", ""),
        "instance": getattr(state, "instance_id", ""),
        "started_at": getattr(state, "started_at", 0.0),
    }


# -- saved documents -------------------------------------------------------
def _store(request: Request) -> SaveStore:
    return request.app.state.saves


def _save_guard(fn):
    """Turn store errors into the right status, never a 500."""
    try:
        return fn()
    except QuotaError as exc:
        # 507: the request was fine, there is no room for it.
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/saves")
def list_folders(request: Request):
    return _save_guard(lambda: [vars(e) for e in _store(request).list_folders()])


@router.post("/saves", status_code=201)
def create_folder(request: Request, body: dict = _BODY):
    name = str(body.get("name", ""))
    return _save_guard(lambda: vars(_store(request).create_folder(name)))


@router.get("/saves/{folder}")
def list_documents(request: Request, folder: str):
    return _save_guard(lambda: [vars(e) for e in _store(request).list_files(folder)])


@router.delete("/saves/{folder}", status_code=204)
def delete_folder(request: Request, folder: str):
    _save_guard(lambda: _store(request).delete_folder(folder))
    return Response(status_code=204)


@router.get("/saves/{folder}/{name}")
def read_document(request: Request, folder: str, name: str):
    return _save_guard(lambda: _store(request).read(folder, name))


@router.put("/saves/{folder}/{name}")
def write_document(request: Request, folder: str, name: str, body: dict = _BODY):
    return _save_guard(lambda: vars(_store(request).write(folder, name, body)))


@router.delete("/saves/{folder}/{name}", status_code=204)
def delete_document(request: Request, folder: str, name: str):
    _save_guard(lambda: _store(request).delete(folder, name))
    return Response(status_code=204)


@router.get("/volumes", response_model=list[VolumeInfoDTO])
def list_volumes(request: Request):
    """Volumes this caller may open.

    Unfiltered unless a Viking session is driving the request, in which case
    the identity server decides.  The bundled demo volume has no counterpart
    there and is always listed.
    """
    volumes = service(request).list_volumes()
    session = getattr(request.state, "viking_session", None)
    if session is None or not request.app.state.viking.config.gate_volumes:
        return volumes
    return [v for v in volumes if session.may_read(v.name)]


@router.get("/viking/session")
def viking_session(request: Request):
    """Who the browser is signed in as, and what Viking says they may see.

    Always answers, so a caller needs no special case: with Viking sign-in off
    it reports ``enabled: false``.  The viewer does not call this today (the
    volume list it shows is ``GET /volumes``, already filtered, and the
    ``unsupported`` grants below are never shown in it); it is for a script or
    a person asking "who am I here, and what was I granted?" when a sign-in
    goes wrong.
    """
    viking = request.app.state.viking
    session = getattr(request.state, "viking_session", None)
    if session is None:
        return {
            "enabled": viking.enabled,
            "signed_in": False,
            "user": getattr(request.state, "user", None),
        }
    return {
        "enabled": True,
        "signed_in": True,
        "user": session.user,
        "user_id": session.user_id,
        "volumes": session.volumes,
        # Volumes Viking shared with this user that this tool cannot render.
        # Reported rather than dropped: "you have access to Wohl but SBFSEM-
        # tools does not support it" is a useful thing to be told.
        "unsupported": session.unsupported,
    }


@router.get("/volumes/{source}", response_model=VolumeInfoDTO)
def volume_info(request: Request, source: str):
    return service(request).volume_info(_source(source))


@router.get("/volumes/{source}/boundary/{layer}/mesh", response_model=MeshDTO)
def boundary_mesh(request: Request, source: str, layer: str,
                  decimate: int = Query(2, ge=1, le=20)):
    """The fitted INL or GCL surface, for context under or over a render."""
    try:
        return service(request).boundary_mesh(_source(source), layer, decimate=decimate)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/volumes/{source}/ipl-thickness")
def ipl_thickness(request: Request, source: str,
                  grid: int = Query(60, ge=10, le=200),
                  near_um: float = Query(25.0, gt=0, le=500)):
    """How thick the volume's IPL is, measured along the INL and GCL surfaces'
    normals and along Z: on a ``grid`` x ``grid`` lattice of columns over the
    two fits' overlap, counting only columns with a marker of each layer within
    ``near_um`` in XY.  The median, 5th and 95th percentiles and range of each
    (µm), their ratio, where it is thinnest and thickest, each layer's tilt and
    the angle between their normals (degrees), and the thickness map.  400 for a
    volume with no boundary markers (Cped, RPC1, RPC2)."""
    try:
        return service(request).ipl_thickness(_source(source), grid=grid, near_um=near_um)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/volumes/{source}/vessels/mesh", response_model=MeshDTO)
def vessel_mesh(request: Request, source: str):
    """Every blood vessel in the volume, as one mesh."""
    try:
        return service(request).vessel_mesh(_source(source))
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/volumes/{source}/search", response_model=list[SearchResultDTO])
def search(request: Request, source: str, label: str = Query(min_length=1),
           exact: bool = False):
    try:
        return service(request).search(_source(source), label, exact=exact)
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/volumes/{source}/resolve-structures", response_model=ResolvedSpecDTO)
def resolve_structures(request: Request, source: str, body: dict = _BODY):
    """Parse a structure specification and resolve its label terms to IDs.

    The grammar is the Viking Data Export portal's: IDs, inclusive ranges,
    and label terms, separated by newline, comma, or semicolon.  A posted
    ``.txt`` file's content goes in ``text`` unchanged.  Per-term failures are
    reported in the result rather than failing the request; only being unable
    to parse the body at all is an error.
    """
    text = body.get("text", "")
    if not isinstance(text, str):
        raise HTTPException(status_code=422, detail="'text' must be a string")
    return service(request).resolve_structure_spec(_source(source), text)


# -- neurons ---------------------------------------------------------------
@router.get("/neurons", response_model=list[NeuronSummaryDTO])
def loaded_neurons(request: Request):
    """Every cell loaded on this server, by anyone.  The one listing that
    names no volume, so the router's guard lets it through: a Viking user is
    shown only the cells of volumes they may open."""
    summaries = service(request).loaded_neurons()
    session = getattr(request.state, "viking_session", None)
    if session is None or not request.app.state.viking.config.gate_volumes:
        return summaries
    return [s for s in summaries if session.may_read(s.source)]


@router.get("/correction-log", response_model=CorrectionLogDTO)
def correction_log(request: Request,
                   ratio: float = Query(PULL_DEFAULT_RATIO, ge=PULL_MIN_RATIO, le=PULL_MAX_RATIO,
                                        description="How many times wider the linked annotation "
                                                    "must be")):
    """The annotations Viking's section correction pulled toward a much wider
    linked one, in every cell loaded on this server under the curve fit
    (``curvefit``, ``all``); see :mod:`sbfsem.analysis.correction_checks`.
    Names no volume, so the router's guard lets it through: as ``GET
    /neurons``, a Viking user is shown only the volumes they may open.

    ``ratio`` sets this log's test only; the corrections service does not take
    one yet (``ratio_sent_to_viking``).
    """
    cells = service(request).correction_log(ratio)
    session = getattr(request.state, "viking_session", None)
    if session is not None and request.app.state.viking.config.gate_volumes:
        cells = [c for c in cells if session.may_read(c.source)]
    return CorrectionLogDTO(ratio=ratio, default_ratio=PULL_DEFAULT_RATIO, min_ratio=PULL_MIN_RATIO,
                            max_ratio=PULL_MAX_RATIO, ratio_sent_to_viking=False, cells=cells)


def _null_is_false(value):
    """The body's flags read ``null`` as false, as ``bool(None)`` always did."""
    return False if value is None else value


_Flag = Annotated[bool, BeforeValidator(_null_is_false)]


class LoadNeuronBody(BaseModel):
    """``POST /neurons``: which cell to load, and how.

    A typed body, so a flag sent as a string (``"false"``) is read as the
    boolean it spells and not as a non-empty string, which ``bool()`` made true.
    """

    id: int
    #: Omitted means Inferior Monkey, as it always did: the router's guard
    #: cannot see a default, so the route checks the volume itself.  ``null``
    #: is no volume at all, a 404 as before.
    source: str | None = "i"
    include_synapses: _Flag = True
    transform: str | None = "Standard"
    refresh: _Flag = False
    #: A mode (off, neighbor, curvefit, all) or, as before, true/false
    #: (true = neighbor); absent = the server's default.
    section_corrections: Any = None
    live_registration: bool | None = None
    check_fresh: _Flag = True
    #: A person's own moves of the cell's annotations, by the setting they were
    #: made under (a copy tag, ``"curvefit.1"``): ``[{"id", "dx_um", "dy_um",
    #: "dz"}]`` each (``sbfsem.core.moves``).  The set of the setting loaded is
    #: applied after the correction; none is written to Viking.
    manual_moves: dict[str, list[dict[str, Any]]] | None = None


@router.post("/neurons", response_model=NeuronDetailDTO)
def load_neuron(request: Request, body: LoadNeuronBody):
    svc = service(request)
    source = _source(body.source or "")  # null or blank names no volume: the 404 it always got
    _require_volume(request, source)
    neuron_id = body.id
    try:
        corrections = body.section_corrections
        mode = None if corrections is None else correction_mode(corrections)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Bad request body: {exc}") from exc
    try:
        refresh = body.refresh
        live = body.live_registration
        detail = svc.load_neuron(
            neuron_id,
            source,
            include_synapses=body.include_synapses,
            transform=str(body.transform),
            refresh=refresh,
            section_corrections=mode,
            live_registration=None if live is None else bool(live),
            manual_moves=body.manual_moves,
        )
        detail.attached_render = _cell_renders(request).get(source, neuron_id)
        # A load served from the disk cache may be months old; one small live
        # query says whether Viking has moved on.  Best effort: Viking being
        # unreachable is not a reason to fail a load that the cache satisfied.
        if body.check_fresh and not refresh and not svc.offline:
            try:
                detail.freshness = svc.freshness(neuron_id, source)
            except (ODataError, ValueError) as exc:
                log.info("freshness check skipped for c%s: %s", neuron_id, exc)
        return detail
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/neurons/import", response_model=NeuronDetailDTO)
def import_neuron(request: Request, file: UploadFile):
    """Load a neuron from an uploaded JSON export (schema v2 or MATLAB-era).

    A plain ``def``, so the framework runs it in a worker thread: reading and
    parsing up to 64 MB of JSON, and registering the cell, held the event loop
    (every other request waited) while this was ``async``.  The upload itself is
    parsed by the framework before the route runs.

    A file is held under its own settings (the correction and registration it was
    saved with), and replaces the copy of that cell already held with the same
    ones -- by design, so a file edited and imported again is what is shown.
    """
    from sbfsem.export.json_v2 import read_neuron_json

    payload = file.file.read()
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(payload)
        temp = Path(fh.name)
    try:
        neuron = read_neuron_json(temp)
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail=f"Unreadable neuron JSON: {exc}") from exc
    finally:
        temp.unlink(missing_ok=True)
    # The volume this file names is inside a multipart upload, so the guard on
    # the router could not see it; check it here, once the file is parsed.
    _require_volume(request, neuron.source)
    return service(request).add_neuron(neuron)


@router.get("/neurons/{source}/{neuron_id}", response_model=NeuronDetailDTO)
def neuron_detail(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    detail = svc.neuron_detail(neuron_id, src)
    detail.attached_render = _cell_renders(request).get(src, neuron_id)
    return detail


# -- render settings attached to a cell ------------------------------------
def _cell_renders(request: Request) -> CellRenderStore:
    return request.app.state.cell_renders


@router.get("/neurons/{source}/{neuron_id}/render-prefs")
def get_render_prefs(request: Request, source: str, neuron_id: CellId):
    """The render settings attached to this cell on the server, if any."""
    return {"render": _cell_renders(request).get(_source(source), neuron_id)}


# ``PUT .../render-prefs`` is below, after ``MeshOptionsBody``: its body is built on it.


@router.delete("/neurons/{source}/{neuron_id}/render-prefs")
def delete_render_prefs(request: Request, source: str, neuron_id: CellId):
    """Remove the render settings attached to this cell."""
    return {"removed": _cell_renders(request).delete(_source(source), neuron_id)}


@router.delete("/neurons/{source}/{neuron_id}")
def unload_neuron(request: Request, source: str, neuron_id: int):
    removed = service(request).unload_neuron(neuron_id, _source(source))
    if not removed:
        raise HTTPException(status_code=404, detail=f"c{neuron_id} is not loaded")
    return {"removed": True}


@router.get("/neurons/{source}/{neuron_id}/nodes", response_model=list[NodeDTO])
def neuron_nodes(request: Request, source: str, neuron_id: int, cell_only: bool = True):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.nodes(neuron_id, src, cell_only=cell_only)


@router.get("/neurons/{source}/{neuron_id}/synapse-points", response_model=list[SynapsePointDTO])
def neuron_synapse_points(request: Request, source: str, neuron_id: int):
    """Every synapse of the cell as one point (its structure ID, type, and the
    annotation it is drawn at), for GraphApp's synapse dots."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.synapse_points(neuron_id, src)


@router.get("/volumes/{source}/locations/{location_id}", response_model=LocationDTO)
def locate_annotation(request: Request, source: str, location_id: int):
    """Where one Viking annotation is: from a loaded cell, else from Viking."""
    svc = service(request)
    src = _source(source)
    try:
        return svc.locate(location_id, src)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc


@router.get("/neurons/{source}/{neuron_id}/edges", response_model=list[EdgeDTO])
def neuron_edges(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.edges(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/last-modified")
def neuron_last_modified(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        row = svc.last_modified_annotation(neuron_id, src)
    except ODataError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Viking query failed: {exc}") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="No annotations found")
    return row


@router.get("/neurons/{source}/{neuron_id}/freshness", response_model=FreshnessDTO)
def neuron_freshness(request: Request, source: str, neuron_id: int):
    """Has Viking edited this cell since the loaded data was fetched?"""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.freshness(neuron_id, src)
    except ODataError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Viking query failed: {exc}") from exc


class FreshnessBatchBody(BaseModel):
    cells: list[dict] = Field(default_factory=list, max_length=500)


@router.post("/neurons/freshness")
def neurons_freshness(request: Request, body: FreshnessBatchBody):
    """Freshness of many loaded cells at once: ``{"cells": [{source, id, copy?}]}``.

    One live request per volume, whatever the number of cells (Viking's
    ``StructureFreshnessBatch``); ``copy`` names the copy of the cell the
    page holds.  A cell that is not loaded, or whose volume cannot be asked,
    comes back with ``freshness: null`` and its ``error`` rather than failing
    the whole batch.
    """
    svc = service(request)
    wanted = []
    refused: list[tuple[str, int]] = []
    for cell in body.cells:
        try:
            src = _source(str(cell.get("source", "i")))
            neuron_id = int(cell["id"])
        except (KeyError, TypeError, ValueError, HTTPException):
            continue
        try:
            _require_volume(request, src)
        except HTTPException:
            # Never asked of Viking: the batch answers for the volumes this
            # account may open, and says so for the rest.
            refused.append((src, neuron_id))
            continue
        copy = cell.get("copy")
        wanted.append((neuron_id, src, str(copy) if copy else None))
    out = []
    for (neuron_id, src, _copy), answer in zip(wanted, svc.freshness_many(wanted), strict=True):
        entry: dict = {"source": src, "id": neuron_id, "freshness": None}
        if isinstance(answer, Exception):
            entry["error"] = str(answer)
        else:
            entry["freshness"] = answer.model_dump()
        out.append(entry)
    for src, neuron_id in refused:
        out.append({"source": src, "id": neuron_id, "freshness": None,
                    "error": f"Your Viking account has no access to {src}."})
    return {"cells": out}


@router.get("/neurons/{source}/{neuron_id}/skeleton", response_model=SkeletonDTO)
def neuron_skeleton(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.skeleton(neuron_id, src)


def _annotation_wire_options(
    gain: float = Query(0.5, ge=0, le=3),
    size_tolerance: float = Query(1.5, ge=1, le=10),
    break_ratio: float = Query(2.5, ge=1, le=100),
    fit: str = Query("quadratic", pattern="^(quadratic|mean)$"),
    lines: int = Query(12, ge=0, le=64),
    ring_spacing_um: float = Query(0.0, ge=0, le=20),
) -> dict:
    """The annotation wireframe's settings, the same for drawing and export."""
    return {
        "gain": gain, "size_tolerance": size_tolerance, "break_ratio": break_ratio,
        "fit": fit, "lines": lines, "ring_spacing_um": ring_spacing_um,
    }


@router.get("/neurons/{source}/{neuron_id}/annotation-wire", response_model=AnnotationWireDTO)
def neuron_annotation_wire(
    request: Request, source: str, neuron_id: int,
    options: dict = Depends(_annotation_wire_options),
):
    """The annotations as a wireframe, averaged along Z at each ring's own scale."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.annotation_wire(neuron_id, src, **options)


def mesh_params(
    method: str = "cylinder",
    smooth_iterations: int = Query(1, ge=0, le=50),
    reduce_fraction: float = Query(1.0, gt=0, le=1),
    pathway: str = Query("original",
                         pattern="^(original|adaptive_taubin|voxel|voxel2|wrap|convolution|ultraliser)$"),
    post_order: str = Query("smooth_first", pattern="^(smooth_first|decimate_first)$"),
    voxel_detail: float = Query(1.0, gt=0.2, le=3),
    voxel_smooth_um: float = Query(0.5, ge=0, le=2),
    tie_um: float = Query(20.0, ge=0, le=100),
    ring_arc_um: float = Query(0.35, ge=0.1, le=1.5),
    wrap_spacing_um: float = Query(0.4, ge=0.15, le=2.0),
    wrap_blend: float = Query(2.0, ge=1.2, le=6.0),
    wrap_reject_sigma: float = Query(2.0, ge=0.5, le=6.0),
    wrap_passes: int = Query(2, ge=0, le=6),
    voxel2_um: float = Query(0.0, ge=0, le=0.3),
    voxel2_faces: int = Query(400_000, ge=0, le=5_000_000),
    voxel2_blend_um: float = Query(0.0, ge=0, le=1.0),
    exact_tube_deg: float = Query(0.0, ge=0, le=60),
    conv_support: float = Query(2.0, ge=1.2, le=4.0),
    ultra_soma: str = Query("discs", pattern="^(discs|sphere)$"),
    ultra_smooth: int = Query(15, ge=0, le=100),
    ultra_lambda: float = Query(0.5, ge=0, le=1.0),
    ultra_inflate: float = Query(0.52, ge=0, le=1.0),
    meshlab: str = Query("none", pattern="^(none|isotropic|qem|poisson)$"),
    meshlab_edge_um: float = Query(0.25, ge=0.05, le=2.0),
    meshlab_fraction: float = Query(0.25, ge=0.02, le=1.0),
    meshlab_depth: int = Query(9, ge=5, le=11),
    pixel_size_um: float = Query(0.05, gt=0, le=1),
    curve_avg_discs: float = Query(0.0, ge=0, le=3),
    curve_avg_paths: float = Query(0.0, ge=0, le=2),
    curve_links_um: float = Query(0.0, ge=0, le=20),
    curve_bridge_um: float = Query(1.0, ge=0, le=10),
    curve_link_bridge_um: float = Query(5.0, ge=0, le=50),
    curve_tie_um: float = Query(0.0, ge=0, le=20),
    surface_smooth_um: float = Query(0.0, ge=0, le=2),
    clip_z: float | None = None,
    clip_ipl: float | None = None,
    keep_above: bool = True,
    color_by_strata: bool = False,
    ipl_method: str = Query("column", pattern="^(normal|column)$"),
) -> dict:
    """Every render option, shared verbatim by the mesh and mesh-job routes."""
    return {
        "method": method,
        "smooth_iterations": smooth_iterations,
        "reduce_fraction": reduce_fraction,
        "pixel_size_um": pixel_size_um,
        "curve_avg_discs": curve_avg_discs,
        "curve_avg_paths": curve_avg_paths,
        "curve_links_um": curve_links_um,
        "curve_bridge_um": curve_bridge_um,
        "curve_link_bridge_um": curve_link_bridge_um,
        "curve_tie_um": curve_tie_um,
        "surface_smooth_um": surface_smooth_um,
        "clip_z": clip_z,
        "clip_ipl": clip_ipl,
        "keep_above": keep_above,
        "color_by_strata": color_by_strata,
        "ipl_method": ipl_method,
        "pathway": pathway,
        "post_order": post_order,
        "voxel_detail": voxel_detail,
        "voxel_smooth_um": voxel_smooth_um,
        "tie_um": tie_um,
        "ring_arc_um": ring_arc_um,
        "wrap_spacing_um": wrap_spacing_um,
        "wrap_blend": wrap_blend,
        "wrap_reject_sigma": wrap_reject_sigma,
        "wrap_passes": wrap_passes,
        "voxel2_um": voxel2_um,
        "voxel2_faces": voxel2_faces,
        "voxel2_blend_um": voxel2_blend_um,
        "exact_tube_deg": exact_tube_deg,
        "conv_support": conv_support,
        "ultra_soma": ultra_soma,
        "ultra_smooth": ultra_smooth,
        "ultra_lambda": ultra_lambda,
        "ultra_inflate": ultra_inflate,
        "meshlab": meshlab,
        "meshlab_edge_um": meshlab_edge_um,
        "meshlab_fraction": meshlab_fraction,
        "meshlab_depth": meshlab_depth,
    }


@router.get("/neurons/{source}/{neuron_id}/mesh", response_model=MeshDTO)
def neuron_mesh(request: Request, source: str, neuron_id: int,
                params: dict = Depends(mesh_params)):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.render_mesh(neuron_id, src, **params)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# -- mesh jobs --------------------------------------------------------------
# A build can outlast any HTTP timeout (RC2 c1 on a disc pathway runs past
# nginx's five minutes and surfaced as a bare 504), so the app renders through
# jobs: one request starts the build, cheap polls watch it, and the finished
# mesh is collected whenever it is done.


def _job_label(neuron_id: int, source: str, options: dict) -> str:
    """"c1178 NeitzInferiorMonkey voxel2+isotropic": what a job log line is about."""
    pathway = options.get("method", "cylinder") if options.get("method") != "cylinder" \
        else options.get("pathway", "original")
    meshlab = options.get("meshlab", "none")
    return f"c{neuron_id} {source} {pathway}" + (f"+{meshlab}" if meshlab != "none" else "")


@router.get("/neurons/{source}/{neuron_id}/mesh-job")
def start_mesh_job(request: Request, source: str, neuron_id: int,
                   params: dict = Depends(mesh_params)):
    """Start (or join) a background build of exactly the mesh GET's render.

    A job is shared only between requests for the same copy of the cell.  A
    finished job is kept for ten minutes, and without the copy in its key a
    cell loaded again with another registration setting (or updated from
    Viking) was handed the surface built before: on 2026-09-23, c21768's
    surface stayed put while its annotations moved up to 11 um.
    """
    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    key = (src, int(neuron_id), neuron.load_serial, tuple(sorted(params.items())))
    try:
        weight = svc.estimate_peak_mb(
            neuron_id, src, method=params["method"],
            pixel_size_um=params["pixel_size_um"], pathway=params.get("pathway", "original"),
            meshlab=params.get("meshlab", "none"), meshlab_depth=params.get("meshlab_depth", 9),
            curve_averaging=curve_averaging(params["method"], params),
            voxel2_faces=params.get("voxel2_faces"), voxel2_um=params.get("voxel2_um"),
            curve_options=params, field_options=params, neuron=neuron,
        )
    except Exception:  # noqa: BLE001 - the estimate must never block a build
        weight = 0.0
    job = request.app.state.mesh_jobs.submit(
        key, lambda: _encoded_mesh(svc.render_buffers(neuron_id, src, neuron=neuron, **params)),
        weight_mb=weight, label=_job_label(neuron_id, src, params),
    )
    return {"job_id": job.id, "status": job.status, "weight_mb": weight}


@router.get("/neurons/{source}/{neuron_id}/grid-job")
def start_grid_job(request: Request, source: str, neuron_id: int,
                   params: dict = Depends(mesh_params)):
    """Make sure the server keeps the whole-cell grid of this render (the mesh
    route's options), building it in the background when it does not.

    Asked when move mode starts, so the first Apply builds the moved copy on the
    grid and contours only the blocks its moves touched
    (:meth:`SbfsemService.prepare_grid`).  The job's result is
    ``{ready, built, seconds}``; a technique with no whole-cell grid answers
    ``ready: false`` at once.
    """
    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    key = ("grid", src, int(neuron_id), neuron.load_serial, tuple(sorted(params.items())))
    try:
        weight = svc.estimate_peak_mb(
            neuron_id, src, method=params["method"],
            pixel_size_um=params["pixel_size_um"], pathway=params.get("pathway", "original"),
            curve_averaging=curve_averaging(params["method"], params),
            voxel2_faces=params.get("voxel2_faces"), voxel2_um=params.get("voxel2_um"),
            curve_options=params, field_options=params, neuron=neuron,
        )
    except Exception:  # noqa: BLE001 - the estimate must never block a build
        weight = 0.0
    job = request.app.state.mesh_jobs.submit(
        key, lambda: svc.prepare_grid(neuron_id, src, neuron=neuron, **params),
        weight_mb=weight, kind="grid", label=_job_label(neuron_id, src, params) + " grid",
    )
    return {"job_id": job.id, "status": job.status, "weight_mb": weight}


#: The mesh route's options that cut or color a built mesh: nothing an estimate weighs.
_VIEW_OPTIONS = ("clip_z", "clip_ipl", "keep_above", "color_by_strata", "ipl_method")


@router.get("/neurons/{source}/{neuron_id}/render-estimate")
def render_estimate(request: Request, source: str, neuron_id: int,
                    params: dict = Depends(mesh_params)):
    """What a render with these options would cost, before anything is built.

    The mesh route's options; the answer is for the copy of the cell the page
    loaded (``?copy=``).  For Closed curve and Hybrid: the raster's voxels and
    exposed faces, the triangles, the build's peak memory and what sending it
    adds, the seconds, the page's JSON and heap, the limits each is held to,
    which of them the options are over, and the finest Voxel size that fits
    them all (:mod:`sbfsem.service.estimate`).  Runs in this request, never in
    the mesh build queue: a fraction of a second, kept per copy and options; a
    cell never built with outlines is rasterized once, coarsely, first.
    """
    if params["pixel_size_um"] < ESTIMATE_FINEST_UM:
        # The mesh route takes any pixel above 0 (its refusal names the finest that
        # fits); an estimate scales a count to the pixel asked, and 1e-200 overflowed.
        raise HTTPException(status_code=422, detail=(
            f"pixel_size_um: an estimate is made from {ESTIMATE_FINEST_UM} um (the Voxel "
            f"control's floor) to 1 um, not {params['pixel_size_um']!r}"))
    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    geometry = {k: v for k, v in params.items() if k not in _VIEW_OPTIONS}
    try:
        return svc.render_estimate(neuron_id, src, neuron=neuron,
                                   memory_limit_mb=request.app.state.mesh_jobs.memory_mb,
                                   **geometry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _mesh_object_or_400(svc, neuron_id: int, src, geometry: dict, clip: dict | None = None):
    """``svc.mesh_object`` with the mesh route's answer to a build that cannot run
    (the closed-curve raster over its voxel limit, a missing file): 400 and the
    reason, not a bare 500.  ``clip`` cuts the mesh as the viewer's clip does."""
    try:
        mesh = svc.mesh_object(neuron_id, src, **geometry)
        if clip and (clip["clip_z"] is not None or clip["clip_ipl"] is not None):
            mesh = svc.clipped_mesh(mesh, src, **clip)
        return mesh
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _plain(value: Any) -> Any:
    """A pydantic model as plain JSON types (``model_dump``); anything else as it is."""
    return value.model_dump(mode="json", by_alias=True) if isinstance(value, BaseModel) else value


#: Writes any value, a model nested anywhere in it included, as its model would.
_ANY_JSON: TypeAdapter[Any] = TypeAdapter(Any)


def _json_bytes(value: Any) -> bytes:
    """The JSON body FastAPI would have made of ``value``, minus ``jsonable_encoder``.

    Written in one pass by pydantic's encoder, a model nested in a dict included
    (a finished job's ``result``), with a non-finite float as ``null`` as the
    backstop writes it.  Each poll of a finished error map made it again with
    ``model_dump`` and ``json.dumps``: 0.086 s for c1178's (3.0 MB, 2026-10-20,
    Mac) against 0.02 s.  The bytes are the same but for a number under 1e-4
    (``0.00001`` for ``1e-05``: :class:`~sbfsem_web.json_response.SafeJSONRouter`).

    A value pydantic cannot write (a NumPy scalar) takes the old way: a model is
    dumped to plain JSON types, and that goes to the same ``JSONResponse``, or to
    the general encoder, whose pure-Python walk over every number this function
    otherwise skips (724404b left it that way for every finished error map and
    difference, 2.3 to 4 times slower than the dump).
    """
    try:
        return _ANY_JSON.dump_json(value, by_alias=True)
    except PydanticSerializationError:
        pass
    plain = {k: _plain(v) for k, v in value.items()} if isinstance(value, dict) else _plain(value)
    try:
        response: Response = JSONResponse(plain)
    except ValueError:
        # A non-finite float in a result: JSON has no NaN, so JSONResponse refuses it.  The
        # backstop every other route has turns it into null, instead of a 500 on every poll.
        response = SafeJSONResponse(plain)
    except TypeError:
        # A value that is neither a model nor plain JSON: the general encoder knows what to do.
        response = SafeJSONResponse(jsonable_encoder(plain))
    return cast(bytes, response.body)  # a JSON response renders to bytes, never a memoryview


def _job_status_response(body: dict, binary: bool = False) -> Response:
    """A job-status dict as the JSON response FastAPI would have made of it,
    minus ``jsonable_encoder``.

    The status has no ``response_model``, so FastAPI ran ``jsonable_encoder``
    over it: ``model_dump`` of the result, then a pure-Python walk over every
    number of it, in the event loop, which no other request can interrupt.
    Measured on c1178 (2026-10-15): 0.94 s for a finished error map (350k
    numbers, 2.8 MB) and 3.97 s for a finished 200k-vertex mesh (2 M numbers,
    14.5 MB), each poll of that job blocking every request for that long.  The
    result is already JSON-shaped after ``model_dump(mode="json")``, so the
    walk changes nothing; the dict goes straight to the same ``JSONResponse``
    (0.21 s and 0.83 s, byte for byte the same body), and, being built inside
    this sync endpoint, in the thread pool, not the event loop.

    A finished mesh is held already encoded (:func:`_encoded_mesh`).  With ``binary``
    (the page's ``Accept``) an :class:`~sbfsem_web.mesh_binary.EncodedMesh` goes
    out as it is; to anyone else it is spliced in as the JSON text it stands
    for, the same bytes as before it was held that way.  A mesh held as
    :class:`EncodedJSON` (one the binary cannot carry exactly), a measurement's
    result (always held as its text, :func:`_encoded_result`), and every status
    without a finished mesh, is JSON whatever was asked.
    """
    mesh, result = body.get("mesh"), body.get("result")
    if isinstance(mesh, mesh_binary.EncodedMesh):
        if binary:
            status = {key: value for key, value in body.items() if key != "mesh"}
            return Response(content=mesh.reply(status), media_type=mesh_binary.MEDIA_TYPE,
                            headers={"Vary": "Accept"})
        mesh = EncodedJSON(_json_bytes(mesh.plain()))
    if isinstance(mesh, EncodedJSON) or isinstance(result, EncodedJSON):
        head = _json_bytes({key: value for key, value in body.items() if key not in ("mesh", "result")})
        # One join, one copy of the text: chained ``+`` copied the result once per operand.
        content = b"".join((head[:-1], b',"mesh":', _text(mesh), b',"result":', _text(result), b"}"))
        return Response(content=content, media_type="application/json", headers={"Vary": "Accept"})
    return Response(content=_json_bytes(body), media_type="application/json", headers={"Vary": "Accept"})


def _text(value: Any) -> bytes:
    """``value`` as JSON: held text as it is, anything else written now."""
    return value if isinstance(value, EncodedJSON) else _json_bytes(value)


def _encoded_mesh(mesh) -> mesh_binary.EncodedMesh | EncodedJSON:
    """A finished render, made into what its poll will send.

    Held in place of the model for the ten minutes a finished job lingers: a
    400,000-vertex mesh is 228 MB as pydantic lists, 41 MB as JSON text and
    14 MB as the binary the page collects (:mod:`sbfsem_web.mesh_binary`), and
    every finished render of every open tab used to pin the largest.  Made in
    the job's thread, so neither the poll nor the event loop pays for it.  A
    mesh the binary cannot carry exactly is held as its JSON text instead.

    A render comes as its arrays (:class:`~sbfsem.service.facade.MeshBuffers`)
    and is encoded from them: its lists, 380 bytes a triangle in this process
    for as long as the encoding took (2,718 MB for 10.6 s on a 7.07 M-triangle
    surface, production 2026-10-20), are made only for a mesh that stays JSON.
    """
    if isinstance(mesh, MeshBuffers):
        encoded = mesh_binary.encode_arrays(_plain(mesh.head), mesh.vertices, mesh.faces, mesh.vertex_data)
        return encoded if encoded is not None else EncodedJSON(_json_bytes(mesh.dto()))
    plain = _plain(mesh)
    encoded = mesh_binary.encode(plain) if isinstance(plain, dict) else None
    return encoded if encoded is not None else EncodedJSON(_json_bytes(plain))


def _encoded_result(result: Any) -> Any:
    """A finished measurement (an error map, a difference), made into the JSON
    text its poll will send, the same bytes the model makes.

    Held in place of the model for the ten minutes a finished job lingers, as a
    render is (:func:`_encoded_mesh`): Inferior c1178's error map of 3.54 M
    vertices held 277 MB as the model's lists and is 50 MB as text, and its poll
    went from +116 MB and 0.49 s to a copy of the text (production, 2026-10-20).
    The result comes as its arrays (:class:`~sbfsem.service.facade.ResultBuffers`):
    the model is written with those lists empty and each list is put in its
    place, written by :func:`~sbfsem_web.json_response.json_float_parts`, or by
    pydantic from its numbers where that cannot vouch for every byte.  Anything
    else is held as it came.
    """
    if not isinstance(result, ResultBuffers):
        return result
    text = _json_bytes(result.head)
    places = []
    for name, values in result.arrays.items():
        empty = b'"' + name.encode() + b'":[]'
        at = text.find(empty)
        if at < 0 or text.find(empty, at + 1) >= 0:  # not one place to put it: the model's way
            return EncodedJSON(_json_bytes(result.dto()))
        listed = json_float_parts(values, result.decimals)
        places.append((at + len(empty) - 2, listed if listed is not None else [_json_bytes(values.tolist())]))
    parts, at = [], 0
    for where, listed in sorted(places, key=lambda place: place[0]):
        parts += [text[at:where], *listed]
        at = where + 2  # past the "[]" it replaces
    parts.append(text[at:])
    return EncodedJSON(b"".join(parts))  # the numbers' one copy


def _measured(make: Callable[[], Any]) -> Any:
    """A measurement job's work: ``make``'s result held as its text
    (:func:`_encoded_result`), then the memory the writing used handed back to
    the system, as the measurement hands back its own."""
    text = _encoded_result(make())
    trim_memory(collect=False)
    return text


def _job_status_body(job) -> dict:
    """What a poll of a job says: where it is, and, once done, its result."""
    # One look at the status: the thread that finishes the job sets the result
    # before the status, so a poll that sees "done" sees the result with it.
    status = job.status
    return {
        "job_id": job.id,
        "status": status,
        "waiting": job.waiting,
        "progress": job.progress if status == "running" else None,
        "seconds": round(job.seconds, 1),
        "detail": job.detail,
        "kind": job.kind,
        "failed_stage": job.failed_stage,
        # A render's result is its mesh; a measurement's has no geometry (the
        # client holds the mesh and the result says which one it is for); a
        # report's is what the file is (GET /api/compare-reports/{id} sends it).
        "mesh": job.result if status == "done" and job.kind == "mesh" else None,
        "result": (job.result.meta() if job.kind == "report" else job.result)
        if status == "done" and job.kind != "mesh" else None,
    }


#: The longest a poll waits for its job to end (``?wait=``), seconds: long
#: enough that a page following a build sends about one poll a second, short
#: enough that the progress it shows stays live.
JOB_WAIT_MAX_S = 1.0


@router.get("/mesh-jobs/{job_id}")
async def mesh_job_status(
    request: Request, job_id: str,
    wait: Annotated[float, Query(ge=0, description="Seconds to wait for a queued or running job to "
                                 "end before answering, at most 1 (more is held to 1)")] = 0.0,
):
    """Where a job is, and once it is done its product.  A finished mesh goes out
    as binary to a client whose ``Accept`` names ``application/x-sbfsem-mesh``
    (the page), as JSON to every other (docs/api.md).

    With ``?wait=`` the answer waits for the job to end, at most ``JOB_WAIT_MAX_S``.
    The wait is in the event loop and holds no thread (:meth:`MeshJobStore.wait`);
    the answer, which for a finished render is megabytes, is put together in the
    thread pool, as it was when this route was a plain ``def``.
    """
    jobs = request.app.state.mesh_jobs
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="No such mesh job (it may "
                            "have expired, or the server restarted)")
    if wait > 0:
        await jobs.wait(job, min(wait, JOB_WAIT_MAX_S))
    binary = mesh_binary.accepts(request.headers.get("accept"))
    return await run_in_threadpool(lambda: _job_status_response(_job_status_body(job), binary=binary))


@router.delete("/mesh-jobs/{job_id}")
def cancel_mesh_job(request: Request, job_id: str):
    """Stop a queued job; a running build cannot be interrupted, and its
    result lands in the mesh cache harmlessly."""
    return {"cancelled": request.app.state.mesh_jobs.cancel(job_id)}


#: The longest pane picture a report request may carry, as base64 text (about
#: 4.5 MB of PNG: a full-screen pane at a device pixel ratio of 2 is ~1-3 MB).
REPORT_SNAPSHOT_CHARS = 6_000_000


class MeshOptionsBody(BaseModel):
    """One side of a mesh diff; the same options as the mesh endpoint."""

    method: str = "cylinder"
    smooth_iterations: int = Field(default=1, ge=0, le=50)
    reduce_fraction: float = Field(default=1.0, gt=0, le=1)
    pathway: str = Field(
        default="original", pattern="^(original|adaptive_taubin|voxel|voxel2|wrap|convolution|ultraliser)$"
    )
    post_order: str = Field(
        default="smooth_first", pattern="^(smooth_first|decimate_first)$"
    )
    voxel_detail: float = Field(default=1.0, gt=0.2, le=3)
    voxel_smooth_um: float = Field(default=0.5, ge=0, le=2)
    tie_um: float = Field(default=20.0, ge=0, le=100)
    ring_arc_um: float = Field(default=0.35, ge=0.1, le=1.5)
    wrap_spacing_um: float = Field(default=0.4, ge=0.15, le=2.0)
    wrap_blend: float = Field(default=2.0, ge=1.2, le=6.0)
    wrap_reject_sigma: float = Field(default=2.0, ge=0.5, le=6.0)
    wrap_passes: int = Field(default=2, ge=0, le=6)
    voxel2_um: float = Field(default=0.0, ge=0, le=0.3)
    voxel2_faces: int = Field(default=400_000, ge=0, le=5_000_000)
    voxel2_blend_um: float = Field(default=0.0, ge=0, le=1.0)
    exact_tube_deg: float = Field(default=0.0, ge=0, le=60)
    conv_support: float = Field(default=2.0, ge=1.2, le=4.0)
    ultra_soma: str = Field(default="discs", pattern="^(discs|sphere)$")
    ultra_smooth: int = Field(default=15, ge=0, le=100)
    ultra_lambda: float = Field(default=0.5, ge=0, le=1.0)
    ultra_inflate: float = Field(default=0.52, ge=0, le=1.0)
    meshlab: str = Field(default="none", pattern="^(none|isotropic|qem|poisson)$")
    meshlab_edge_um: float = Field(default=0.25, ge=0.05, le=2.0)
    meshlab_fraction: float = Field(default=0.25, ge=0.02, le=1.0)
    meshlab_depth: int = Field(default=9, ge=5, le=11)
    pixel_size_um: float = Field(default=0.05, gt=0, le=1)
    curve_avg_discs: float = Field(default=0.0, ge=0, le=3)
    curve_avg_paths: float = Field(default=0.0, ge=0, le=2)
    curve_links_um: float = Field(default=0.0, ge=0, le=20)
    curve_bridge_um: float = Field(default=1.0, ge=0, le=10)
    curve_link_bridge_um: float = Field(default=5.0, ge=0, le=50)
    curve_tie_um: float = Field(default=0.0, ge=0, le=20)
    surface_smooth_um: float = Field(default=0.0, ge=0, le=2)


class AttachedRenderBody(MeshOptionsBody):
    """What a cell's attached render holds: the mesh options, and the view's own.

    The viewer sends its whole render (``normalizeRender``), which carries the
    cut and the strata coloring as well as the geometry.  They are the mesh
    route's four extra options, and the diff and deviation bodies leave them
    out on purpose (a clipped mesh cannot be measured against), so this body
    is not ``MeshOptionsBody``: attaching a render used to drop ``clip_ipl``,
    ``keep_above`` and ``color_by_strata`` without a word.
    """

    clip_z: float | None = None
    clip_ipl: float | None = None
    keep_above: bool = True
    color_by_strata: bool = False
    #: Taken as the mesh route takes it, and never stored: how depth is measured
    #: is one setting for the whole page (its Settings tab), not a cell's.
    ipl_method: str = Field(default="column", pattern="^(normal|column)$")


@router.put("/neurons/{source}/{neuron_id}/render-prefs")
def put_render_prefs(request: Request, source: str, neuron_id: CellId,
                     body: AttachedRenderBody):
    """Attach the given render settings to this cell for every future load."""
    # A cut that is off is left out, not stored as null: the browser reads a
    # missing option as its default and a null as a value.
    render = body.model_dump(exclude_none=True)
    render.pop("ipl_method", None)
    try:
        _cell_renders(request).set(_source(source), neuron_id, render)
    except QuotaError as exc:
        raise HTTPException(status_code=507, detail=str(exc)) from exc
    except SaveError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"render": render}


class MeshDiffBody(BaseModel):
    a: MeshOptionsBody = Field(default_factory=lambda: MeshOptionsBody())
    b: MeshOptionsBody = Field(default_factory=lambda: MeshOptionsBody())
    samples: int = Field(default=250_000, ge=10_000, le=1_000_000)
    #: What signs the distance: B's outward normal at the nearest surface sample
    #: (``normal``, the default) or whether B's solid encloses the vertex
    #: (``enclosure``: a ray count over B with its open ends closed, and B's
    #: winding number where closing an end would invent a sheet; tens of
    #: seconds and several hundred MB on a large cell, so a job only;
    #: ``mesh-diff`` refuses it).
    sign: Literal["normal", "enclosure"] = "normal"
    #: Also measure B against A (a job only; ``mesh-diff`` refuses it).
    both: bool = False
    #: The copy of the cell mesh B is built from and held to (a summary's
    #: ``copy_tag``), when it is not mesh A's: a surface before and after a
    #: setting that moves the annotations.  Mesh A's copy is the request's
    #: ``?copy=``, as for every route of the cell.  A job only (``mesh-diff``
    #: refuses it); a copy that is not held is a 404.
    copy_b: str | None = Field(default=None, min_length=1, max_length=64)


@router.post("/neurons/{source}/{neuron_id}/mesh-deviation-job")
def start_deviation_job(request: Request, source: str, neuron_id: int, body: MeshOptionsBody):
    """Start (or join) the measurement of one rendering against the cell's annotations.

    The request that starts a measurement never carries it: a large cell's
    error map takes minutes (c1178 through MeshLab isotropic: about 290
    CPU-seconds), and nginx answers 504 after five.  Poll
    ``/api/mesh-jobs/{id}``; the result has no geometry.  A job is shared
    only between requests for the same copy of the cell and the same options.
    """
    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    options = body.model_dump()
    weight = svc.measure_weight_mb(neuron, options)
    key = ("deviation", src, int(neuron_id), neuron.load_serial, tuple(sorted(options.items())))
    job = request.app.state.mesh_jobs.submit(
        key, lambda: _measured(lambda: svc.deviation_buffers(neuron, options)), weight_mb=weight,
        kind="deviation",
        label=_job_label(neuron_id, src, options),
    )
    return {"job_id": job.id, "status": job.status, "kind": job.kind, "weight_mb": weight}


@router.post("/neurons/{source}/{neuron_id}/mesh-diff-job")
def start_diff_job(request: Request, source: str, neuron_id: int, body: MeshDiffBody):
    """Start (or join) the signed difference of two renderings, as a job (see
    :func:`start_deviation_job`).

    Mesh A is built from, and held to, the copy of the cell the request names
    (``?copy=``); mesh B from the same copy unless ``copy_b`` names another, so a
    surface before a setting that moves the annotations can be held to the
    surface after it.  The job is shared only between requests for the same two
    copies: both serials are in its key.
    """
    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    neuron_b = neuron
    if body.copy_b:
        with requested_copy(body.copy_b):
            neuron_b = _neuron_or_404(svc, neuron_id, src)
    options_a, options_b = body.a.model_dump(), body.b.model_dump()
    weight = svc.measure_weight_mb(neuron, options_a, options_b)
    key = ("diff", src, int(neuron_id), neuron.load_serial, neuron_b.load_serial,
           tuple(sorted(options_a.items())), tuple(sorted(options_b.items())),
           body.samples, body.sign, body.both)
    label = f"{_job_label(neuron_id, src, options_a)} vs {_job_label(neuron_id, src, options_b)}"
    if neuron_b is not neuron:
        label += (f" [{getattr(neuron, 'copy_tag', None)}"
                  f" vs {getattr(neuron_b, 'copy_tag', None)}]")
    job = request.app.state.mesh_jobs.submit(
        key, lambda: _measured(lambda: svc.diff_buffers(neuron, options_a, options_b, body.samples,
                                                        sign=body.sign, both=body.both,
                                                        neuron_b=None if neuron_b is neuron else neuron_b)),
        weight_mb=weight, kind="diff", label=label,
    )
    return {"job_id": job.id, "status": job.status, "kind": job.kind, "weight_mb": weight}


class ReportRenderingBody(BaseModel):
    """One rendering of a Render compare report: a layer as it was built."""

    #: The options the layer's surface was built with (its snapshot).
    options: MeshOptionsBody
    label: str = Field(min_length=1, max_length=120)
    #: The layer's color on screen.
    color: str = Field(pattern=r"^#[0-9a-fA-F]{6}$")
    pane: int = Field(ge=1, le=6)
    layer: int = Field(ge=1, le=4)
    #: Also hold it to the annotations (its error map page).
    deviation: bool = True
    #: Its panel was changed after it was built (the report says so).
    stale: bool = False
    #: The copy of the cell it was built from (a load summary's ``copy_tag``), when it
    #: is not the request's (``?copy=``): a layer on another registration.
    copy_tag: str | None = Field(default=None, min_length=1, max_length=64)
    #: That registration in words, as the page says it ("Current transforms + Field + fit").
    registration: str = Field(default="", max_length=120)


class ReportPaneBody(BaseModel):
    """A pane of the compare grid, as the page showed it."""

    number: int = Field(ge=1, le=6)
    kind: Literal["layers", "difference"] = "layers"
    layers: int = Field(default=0, ge=0, le=4)
    #: A Difference pane: the two renderings it measures (indices into ``renderings``).
    difference: tuple[int, int] | None = None
    #: A picture of the pane, as a ``data:image/png;base64,`` or
    #: ``data:image/jpeg;base64,`` URL (left out of the report when it is neither).
    snapshot: str | None = Field(default=None, max_length=REPORT_SNAPSHOT_CHARS)


class ReportCameraBody(BaseModel):
    """The panes' camera for the report's "This view": from what is looked at
    toward the camera, its up vector (both in the scene's frame) and Flip Z."""

    direction: tuple[float, float, float]
    up: tuple[float, float, float]
    flip_z: bool = False


#: A viewpoint a report draws from: the panes' camera, or one of the viewer's named views.
ReportView = Literal["view", "xy1", "xz1", "yz1"]
_REPORT_VIEWS: list[ReportView] = ["xy1", "xz1"]


class CompareReportBody(BaseModel):
    """What a Render compare report holds (``POST .../compare-report-job``)."""

    renderings: list[ReportRenderingBody] = Field(min_length=1, max_length=24)
    #: Pairs of renderings to measure both ways (indices into ``renderings``).
    pairs: list[tuple[int, int]] = Field(default_factory=list, max_length=276)
    panes: list[ReportPaneBody] = Field(default_factory=list, max_length=6)
    #: The viewpoints every picture is drawn from: the panes' camera ("view", which
    #: needs ``camera``) and the viewer's XY1, XZ1 and YZ1.
    views: list[ReportView] = Field(default_factory=lambda: list(_REPORT_VIEWS), min_length=1, max_length=4)
    camera: ReportCameraBody | None = None
    sign: Literal["normal", "enclosure"] = "enclosure"
    addendum: bool = True
    page: Literal["letter", "a4"] = "letter"
    #: The browser's date, time and zone, for the cover.
    created: str = Field(default="", max_length=64)


#: The pictures a report request may carry, by data URL prefix, with the bytes
#: each must start with.
_PICTURE_KINDS = {"data:image/png;base64,": b"\x89PNG\r\n\x1a\n", "data:image/jpeg;base64,": b"\xff\xd8\xff"}


def _picture_from_data_url(value: str | None) -> bytes | None:
    """The PNG or JPEG a ``data:image/...;base64,`` URL carries, or None for anything else."""
    for prefix, magic in _PICTURE_KINDS.items():
        if value and value.startswith(prefix):
            try:
                data = base64.b64decode(value[len(prefix):], validate=True)
            except (binascii.Error, ValueError):
                return None
            return data if data.startswith(magic) else None
    return None


def _report_copies(svc, neuron, src: str, body: CompareReportBody) -> list:
    """The copy of the cell each rendering was built from: the request's (``?copy=``)
    unless the rendering names another, which must be held (404 otherwise, as a
    difference's ``copy_b``)."""
    held: dict[str, Any] = {getattr(neuron, "copy_tag", None) or "": neuron}
    out = []
    for r in body.renderings:
        tag = r.copy_tag or getattr(neuron, "copy_tag", None) or ""
        if tag not in held:
            with requested_copy(tag):
                held[tag] = _neuron_or_404(svc, int(neuron.id), src)
        out.append(held[tag])
    return out


def _report_spec(neuron, src: str, body: CompareReportBody, copies: list | None = None):
    """The report a request asks for, as the library takes it (400 on a request
    that names no viewpoint it can draw, or a pair that is not two renderings).
    ``copies`` are the renderings' copies of the cell (:func:`_report_copies`)."""
    from sbfsem import __version__
    from sbfsem.export.compare_pdf import ReportPane, ReportRendering, ReportSpec
    from sbfsem.geometry.raster import NAMED_VIEWS, View

    copies = copies or [neuron] * len(body.renderings)
    renderings = tuple(
        ReportRendering(label=r.label, color=r.color.lower(), pane=r.pane, layer=r.layer,
                        options=r.options.model_dump(), deviation=r.deviation, stale=r.stale,
                        copy=getattr(c, "copy_tag", None), registration=r.registration)
        for r, c in zip(body.renderings, copies, strict=True))
    views: list[View] = []
    for key in dict.fromkeys(body.views):
        if key != "view":
            views.append(NAMED_VIEWS[key])
            continue
        camera = body.camera
        if camera is None:
            continue
        numbers = (*camera.direction, *camera.up)
        if not all(math.isfinite(x) for x in numbers) or not any(camera.direction):
            raise ValueError("the camera's direction and up must be finite, and the direction not zero")
        views.append(View("view", "This view (the panes' camera)", camera.direction, camera.up,
                          mirror_z=camera.flip_z))
    if not views:
        raise ValueError("no viewpoint to draw from: 'view' needs the panes' camera")
    n = len(renderings)
    panes = []
    for pane in body.panes:
        difference = pane.difference
        if difference is not None and not (0 <= difference[0] < n and 0 <= difference[1] < n
                                           and difference[0] != difference[1]):
            difference = None
        panes.append(ReportPane(number=pane.number, kind=pane.kind, layers=pane.layers,
                                difference=difference, snapshot=_picture_from_data_url(pane.snapshot)))
    scale = getattr(neuron, "volume_scale", None)
    return ReportSpec(
        cell=int(neuron.id), source=src, volume=neuron.volume.display_name, renderings=renderings,
        pairs=tuple((int(a), int(b)) for a, b in body.pairs), panes=tuple(panes), views=tuple(views),
        sign=body.sign, addendum=body.addendum, page=body.page, created=body.created,
        build=__version__, copy=getattr(neuron, "copy_tag", None),
        z_scale_nm=float(scale[2]) if scale is not None and len(scale) > 2 else None,
    )


@router.post("/neurons/{source}/{neuron_id}/compare-report-job")
def start_compare_report_job(request: Request, source: str, neuron_id: int,
                             body: CompareReportBody):
    """Start (or join) a Render compare report: a color PDF of the renderings
    held to the annotations and every chosen pair to each other, both ways
    (:mod:`sbfsem.export.compare_pdf`).

    A job, like a measurement (minutes for many pairs of a large cell): poll
    ``/api/mesh-jobs/{id}``; its result says what the file is (``filename``,
    ``bytes``, ``pages``), and ``GET /api/compare-reports/{id}`` sends it.  A
    cancel stops it between its steps.  Renderings and measurements come from
    the service's caches, so whatever the panes already built is free.
    """
    from sbfsem.export.compare_pdf import Plan

    svc = service(request)
    src = _source(source)
    neuron = _neuron_or_404(svc, neuron_id, src)
    copies = _report_copies(svc, neuron, src, body)
    try:
        spec = _report_spec(neuron, src, body, copies)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    weight = svc.report_weight_mb(neuron, spec)
    digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
    # Every copy it measures is in the key: reloading any of them is another report.
    serials = tuple(sorted({(getattr(c, "copy_tag", None) or "", c.load_serial) for c in copies}))
    key = ("report", src, int(neuron_id), neuron.load_serial, serials, digest)
    job = request.app.state.mesh_jobs.submit(
        key, lambda: svc.compare_report(neuron, spec, copies), weight_mb=weight, kind="report",
        label=f"c{neuron_id} {src} report: {len(spec.renderings)} renderings, {len(spec.pairs)} pairs",
    )
    return {"job_id": job.id, "status": job.status, "kind": job.kind, "weight_mb": weight,
            "pages": Plan(spec).total}


@router.get("/compare-reports/{job_id}")
def compare_report_file(request: Request, job_id: str):
    """The PDF a finished report job made (``POST .../compare-report-job``), as a
    download.  Held for ten minutes after it is made, like every finished job;
    a signed-in Viking user must be allowed the volume it describes."""
    job = request.app.state.mesh_jobs.get(job_id)
    if job is None or job.kind != "report":
        raise HTTPException(status_code=404, detail="No such report (it may have expired, or "
                            "the server restarted): ask for it again")
    _require_volume(request, str(job.key[1]))
    if job.status != "done":
        raise HTTPException(status_code=409, detail=f"The report is not ready: it is {job.status}")
    report = job.result
    return Response(content=report.pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{report.filename}"',
        "Cache-Control": "no-store",
    })


@router.post("/neurons/{source}/{neuron_id}/mesh-diff", response_model=MeshDiffDTO)
def neuron_mesh_diff(request: Request, source: str, neuron_id: int, body: MeshDiffBody):
    """Signed per-vertex distance between two renderings of one neuron."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    if body.both:
        raise HTTPException(
            status_code=400,
            detail="both ways is a job's result: POST mesh-diff-job (this route returns mesh A only)")
    if body.copy_b:
        raise HTTPException(
            status_code=400,
            detail="a second copy is a job's measurement: POST mesh-diff-job "
                   "(this route holds both renderings to the copy named by ?copy=)")
    if body.sign != "normal":
        # The enclosure test adds 430 to 790 MB and several seconds to a
        # 200,000-vertex difference (more on a cell that is long in Z), and
        # this route holds neither the memory gate nor a job's progress: a
        # few of them at once would be the request-thread 504 the jobs exist
        # to remove.
        raise HTTPException(
            status_code=400,
            detail=f"sign {body.sign!r} is a job's measurement: POST mesh-diff-job "
                   "(this route signs by the normal only)")
    try:
        return svc.mesh_diff(
            neuron_id,
            src,
            options_a=body.a.model_dump(),
            options_b=body.b.model_dump(),
            samples=body.samples,
            sign=body.sign,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/neurons/{source}/{neuron_id}/mesh-deviation", response_model=DeviationMapDTO)
def neuron_mesh_deviation(request: Request, source: str, neuron_id: int,
                          body: MeshOptionsBody):
    """One rendering against the cell's annotations, per vertex and per rim point."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.mesh_deviation(neuron_id, src, options=body.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/neurons/{source}/{neuron_id}/synapses/{synapse_type}/mesh",
            response_model=MeshDTO)
def synapse_mesh(request: Request, source: str, neuron_id: int, synapse_type: str,
                 radius_um: float = Query(0.5, gt=0),
                 shape: str = Query("circle", pattern="^(circle|oval)$")):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.synapse_mesh(
            neuron_id, src, synapse_type, radius_um=radius_um, shape=shape
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/neurons/{source}/{neuron_id}/closed-curves",
            response_model=list[ClosedCurveDTO])
def closed_curves(request: Request, source: str, neuron_id: int,
                  z_section: int | None = None,
                  spline_points: int | None = Query(None, ge=1, le=1000)):
    """The cell's outlines, sent as the service keeps them: already JSON
    (:meth:`~sbfsem.service.facade.SbfsemService.closed_curves_json`)."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    body = svc.closed_curves_json(neuron_id, src, z_section=z_section, spline_points=spline_points)
    return Response(content=body, media_type="application/json")


@router.get("/neurons/{source}/{neuron_id}/closed-curves/mesh", response_model=MeshDTO)
def closed_curve_mesh(request: Request, source: str, neuron_id: int,
                      thickness_um: float | None = None):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.cone_outline_mesh(neuron_id, src, thickness_um=thickness_um)


# -- analyses --------------------------------------------------------------
@router.get("/neurons/{source}/{neuron_id}/stratification", response_model=StratificationDTO)
def stratification(
    request: Request, source: str, neuron_id: int,
    num_bins: int = Query(20, ge=2, le=200),
    include_soma: bool = True,
    omit_outliers: bool = True,
    ipl_margin: float = 0.2,
    ipl_method: IplMethod = "column",
):
    """The cell's IPL stratification.  ``ipl_method``: depth measured along Z
    (``column``, MATLAB's, the API's default) or along the layers' normals
    (``normal``, the viewer's)."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.stratification(
            neuron_id, src, num_bins=num_bins, include_soma=include_soma,
            omit_outliers=omit_outliers, ipl_margin=ipl_margin, ipl_method=ipl_method,
        )
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class StrataExportCell(BaseModel):
    """One cell of a Stratification export: which copy, and how the chart names and colors it."""

    source: str
    id: int = Field(gt=0, le=MAX_CELL_ID)
    copy_tag: str | None = Field(None, max_length=200)
    label: str = Field("", max_length=120)
    color: str = Field("#4c78a8", pattern=r"^#[0-9a-fA-F]{6}$")


class StrataExportBody(BaseModel):
    """The Stratification tab's chart as the page draws it: its cells and settings."""

    cells: list[StrataExportCell] = Field(min_length=1, max_length=50)
    num_bins: int = Field(20, ge=2, le=200)
    include_soma: bool = True
    omit_outliers: bool = True
    ipl_margin: float = Field(0.2, ge=0.0, le=1.0)
    tick_pct: float = Field(20.0, ge=1.0, le=100.0)
    #: How the depths are measured: along Z (MATLAB's) or along the layers' normals.
    ipl_method: IplMethod = "column"
    #: The PDF only: the statistics and binned counts, and each annotation's depth, on the pages after the chart.
    statistics: bool = True
    annotations: bool = False


def _strata_export(request: Request, body: StrataExportBody):
    """The cells and settings of a Stratification export, read from the copies the page names."""
    from sbfsem import __version__
    from sbfsem.core.volumes import Volumes
    from sbfsem.export.stratification import StrataCell, StrataSettings

    svc = service(request)
    # Every volume first: a body naming one the account may not open reads nothing.
    sources = [_source(cell.source) for cell in body.cells]
    for src in dict.fromkeys(sources):
        _require_volume(request, src)
    cells = []
    for cell, src in zip(body.cells, sources, strict=True):
        with requested_copy(cell.copy_tag):
            try:
                result, table = svc.stratification_tables(
                    cell.id, src, num_bins=body.num_bins, include_soma=body.include_soma,
                    omit_outliers=body.omit_outliers, ipl_margin=body.ipl_margin,
                    ipl_method=body.ipl_method)
            except NeuronNotLoadedError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except (ValueError, FileNotFoundError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        cells.append(StrataCell(name=f"c{cell.id}", label=cell.label or f"c{cell.id}",
                                volume=Volumes.from_str(src).display_name, color=cell.color,
                                result=result, annotations=table))
    settings = StrataSettings(num_bins=body.num_bins, include_soma=body.include_soma,
                              omit_outliers=body.omit_outliers, ipl_margin=body.ipl_margin,
                              tick_pct=body.tick_pct, method=body.ipl_method, version=__version__,
                              made=datetime.now(UTC))
    names = "-".join(c.name for c in cells[:4]) + ("-and-more" if len(cells) > 4 else "")
    return cells, settings, f"stratification-{names}"


@router.post("/stratification/export.pdf")
def stratification_export_pdf(request: Request, body: StrataExportBody):
    """The Stratification chart as a vector PDF, with ``statistics`` and
    ``annotations`` the data on the pages after it (``sbfsem.export.stratification``)."""
    from sbfsem.export.stratification import PDF_MAX_ANNOTATION_ROWS, stratification_pdf

    cells, settings, name = _strata_export(request, body)
    rows = sum(len(c.annotations) for c in cells)
    if body.annotations and rows > PDF_MAX_ANNOTATION_ROWS:
        raise HTTPException(status_code=400, detail=(
            f"every annotation's depth would be {rows:,} rows, and the PDF takes at most "
            f"{PDF_MAX_ANNOTATION_ROWS:,}; the Excel workbook (Data .xlsx) has them all"))
    data = stratification_pdf(cells, settings, statistics=body.statistics, annotations=body.annotations)
    return Response(data, media_type="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'})


@router.post("/stratification/export.xlsx")
def stratification_export_xlsx(request: Request, body: StrataExportBody):
    """The Stratification chart's data as an Excel workbook: a Summary sheet with a
    column per cell, and a sheet per cell with every annotation's depth."""
    from sbfsem.export.stratification import stratification_xlsx

    cells, settings, name = _strata_export(request, body)
    data = stratification_xlsx(cells, settings)
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'})


@router.get("/neurons/{source}/{neuron_id}/synapse-stratification/{synapse_type}",
            response_model=StratificationDTO)
def synapse_stratification(request: Request, source: str, neuron_id: int,
                           synapse_type: str, num_bins: int = Query(25, ge=2, le=200),
                           ipl_method: IplMethod = "column"):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.synapse_stratification(neuron_id, src, synapse_type, num_bins=num_bins,
                                          ipl_method=ipl_method)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/neurons/{source}/{neuron_id}/hull", response_model=HullDTO)
def hull(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.dendritic_field_hull(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/ie-ratio", response_model=IERatioDTO)
def ie_ratio(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.ie_ratio(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/annotators", response_model=AnnotatorsDTO)
def neuron_annotators(request: Request, source: str, neuron_id: int,
                      tolerance_s: float = Query(3600.0, ge=0, le=31_536_000)):
    """Who annotated this cell, and what share of it each of them holds now.

    Live from Viking, cell and child structures together.  ``tolerance_s`` is
    how long after an annotation was created it may still be written without
    the write counting as a revision (default one hour).  Viking keeps only
    the last writer of an annotation, so the shares are of the record as it
    stands, not of the work that went into it.
    """
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.annotators(neuron_id, src, tolerance_s=tolerance_s)
    except ODataError as exc:
        raise HTTPException(status_code=502,
                            detail=f"Viking query failed: {exc}") from exc


@router.get("/neurons/{source}/{neuron_id}/skeleton/check", response_model=SkeletonCheckDTO)
def skeleton_check(request: Request, source: str, neuron_id: int):
    """Loops, fragments, jumps, hairpins, spikes, spokes and stubs of the
    cell's annotation graph, each addressed by Viking Location IDs."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.skeleton_check(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/shift-summary", response_model=ShiftSummaryDTO)
def shift_summary(request: Request, source: str, neuron_id: int,
                  pair: str = Query("correction", pattern="^(correction|transforms|both)$")):
    """How far Viking's section correction and the volume's current transforms
    moved the cell's annotations, and whether the links across sections agree
    better afterwards.  Answers from the copy of the cell named by ``?copy=``.

    ``pair`` is the move the link verdict and the coherent sections are about:
    ``correction`` (registered to corrected, the default), ``transforms``
    (stored to registered) or ``both`` (stored to corrected).
    """
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.shift_summary(neuron_id, src, pair)


@router.get("/neurons/{source}/{neuron_id}/registration-quality", response_model=RegistrationQualityDTO)
def registration_quality(request: Request, source: str, neuron_id: int):
    """How the copy of the cell named by ``?copy=`` (a registration) lines it up, and what
    it does to what each section image shows: the moves, the link steps at its positions,
    the sections that moved as one, the change of the offsets between two of the cell's
    annotations on one section and between each synapse and the cell, and the curve fit's
    pulls (``tools/registration_quality.py`` asks it of every registration of many cells).
    """
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.registration_quality(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/correction-pulls", response_model=CorrectionPullsDTO)
def correction_pulls(request: Request, source: str, neuron_id: int,
                     ratio: float = Query(PULL_DEFAULT_RATIO, ge=PULL_MIN_RATIO, le=PULL_MAX_RATIO,
                                          description="How many times wider the linked annotation "
                                                      "must be")):
    """The annotations Viking's curve fit pulled toward a much wider linked one in
    this cell: its rows of ``GET /correction-log``, for the copy named by
    ``?copy=``.  A copy loaded without a section correction is ``off``, not checked.
    """
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.correction_pulls(neuron_id, src, ratio)


@router.get("/neurons/{source}/{neuron_id}/morphometrics", response_model=MorphometricsDTO)
def morphometrics(request: Request, source: str, neuron_id: int,
                  soma_fraction: float = Query(0.8, gt=0, le=1),
                  bracket: bool = Query(
                      False,
                      description="Also compute length_floor_um and "
                                  "length_floor_by_radius_um: the shortest path through "
                                  "every annotation (seconds on a large cell)")):
    """Cable length (3-D and in-plane), longest path from the soma, branch
    points, tips and extent of the cell's traced skeleton."""
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.morphometrics(neuron_id, src, soma_fraction=soma_fraction, bracket=bracket)


@router.get("/neurons/{source}/{neuron_id}/surface-area", response_model=AnalysisResultDTO)
def surface_area(request: Request, source: str, neuron_id: int):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.surface_area(neuron_id, src)


@router.get("/neurons/{source}/{neuron_id}/dendrite-diameter",
            response_model=DendriteDiameterDTO)
def dendrite_diameter(request: Request, source: str, neuron_id: int,
                      bin_width: float = Query(5.0, ge=0.05, le=1000)):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.dendrite_diameter(neuron_id, src, bin_width=bin_width)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/neurons/{source}/{neuron_id}/tortuosity", response_model=AnalysisResultDTO)
def tortuosity(request: Request, source: str, neuron_id: int,
               location_a: int = Query(...), location_b: int = Query(...),
               dim: int = Query(2, ge=2, le=3)):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.tortuosity(neuron_id, src, location_a, location_b, dim)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/neurons/{source}/{neuron_id}/open-ended", response_model=AnalysisResultDTO)
def open_ended(request: Request, source: str, neuron_id: int,
               include_off_edge: bool = True, include_terminal: bool = False):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.open_ended_branches(
        neuron_id, src, include_off_edge=include_off_edge, include_terminal=include_terminal
    )


@router.get("/neurons/{source}/{neuron_id}/synapse-soma-distance/{synapse_type}",
            response_model=AnalysisResultDTO)
def synapse_soma_distance(request: Request, source: str, neuron_id: int, synapse_type: str):
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    return svc.synapse_soma_distance(neuron_id, src, synapse_type)


# -- linked neurons --------------------------------------------------------
@router.get("/neurons/{source}/{neuron_id}/linked-neurons", response_model=LinkedNeuronsDTO)
def linked_neurons(request: Request, source: str, neuron_id: int,
                   order: str = Query("neuron", pattern="^(neuron|type)$"),
                   refresh: bool = False):
    """The ``getAllLinkedNeurons`` table: every synapse and its partner.

    ``order=neuron`` is the MATLAB function's own ordering (by partner);
    ``order=type`` is the lab's ``getAllLinkedNeurons_sorted`` ordering (by
    synapse type, label, partner, synapse).  ``refresh`` re-asks Viking.
    """
    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.linked_neurons_report(neuron_id, src, order=order, refresh=refresh)
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc


@router.get("/neurons/{source}/{neuron_id}/linked-neurons.{fmt}")
def linked_neurons_file(request: Request, source: str, neuron_id: int, fmt: str,
                        order: str = Query("neuron", pattern="^(neuron|type)$")):
    """The same table as a download: ``csv`` or ``xlsx``."""
    from sbfsem.network.links import links_to_csv, links_to_xlsx

    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    if fmt not in ("csv", "xlsx"):
        raise HTTPException(status_code=400, detail="format must be csv or xlsx")
    try:
        table = svc.linked_neurons_table(neuron_id, src, order=order)
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc
    # The volume travels with the ID (ground rule 11): c74420 is a different
    # cell in every volume, so the file says which one it describes.
    filename = f"c{neuron_id}-{src}-linked-neurons.{fmt}"
    if fmt == "csv":
        body: bytes = links_to_csv(table).encode("utf-8")
        media = "text/csv; charset=utf-8"
    else:
        body = links_to_xlsx(table, sheet_name=f"c{neuron_id} {src}")
        media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    return Response(
        content=body, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# -- connectivity (Graffinity) --------------------------------------------
class ConnectivityQueryBody(BaseModel):
    """A Graffinity query: a path length, a label pattern per node position
    and a synapse-type pattern per hop, from an explicit start set or a
    start-label pattern."""

    source: str
    hops: int = Field(2, ge=1, le=4)
    min_hops: int | None = Field(None, ge=1, le=4)
    start_ids: list[int] | None = None
    start_pattern: str | None = None
    node_patterns: list[str] = Field(default_factory=list)
    edge_patterns: list[str] = Field(default_factory=list)
    max_paths: int = Field(20_000, ge=1, le=200_000)
    max_frontier: int = Field(300, ge=1, le=2_000)
    direction: str = Field("downstream", pattern="^(downstream|upstream)$")
    #: Let a neuron repeat within a path, as Graffinity's search did.
    allow_repeats: bool = False
    refresh: bool = False


@router.post("/connectivity/query", response_model=ConnectivityDTO)
def connectivity_query(request: Request, body: ConnectivityQueryBody):
    svc = service(request)
    src = _source(body.source)
    # The router's guard reads this body too, but only as well as it parses: the
    # route reads the body FastAPI parsed, which is the volume it will query.
    _require_volume(request, src)
    if not body.start_ids and not (body.start_pattern or "").strip():
        raise HTTPException(status_code=422, detail="give start_ids or a start_pattern")
    if body.min_hops is not None and body.min_hops > body.hops:
        raise HTTPException(status_code=422, detail="min_hops cannot exceed hops")
    try:
        return svc.connectivity_query(
            src, hops=body.hops, min_hops=body.min_hops, start_ids=body.start_ids,
            start_pattern=body.start_pattern, node_patterns=body.node_patterns,
            edge_patterns=body.edge_patterns, max_paths=body.max_paths,
            max_frontier=body.max_frontier, direction=body.direction,
            allow_repeats=body.allow_repeats, refresh=body.refresh,
        )
    except re.error as exc:
        raise HTTPException(status_code=400, detail=f"bad pattern: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc


@router.get("/connectivity/{source}/graph")
def connectivity_graph_size(request: Request, source: str):
    """How much of the volume's link graph this server has fetched so far."""
    return service(request).link_graph_size(_source(source))


# -- network ---------------------------------------------------------------
@router.get("/network", response_model=NetworkDTO)
def network(request: Request, source: str, ids: str = Query(...)):
    svc = service(request)
    src = _source(source)
    try:
        neuron_ids = [int(v) for v in ids.split(",") if v.strip()]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="ids must be comma-separated integers") from exc
    for neuron_id in neuron_ids:
        _neuron_or_404(svc, neuron_id, src)
    try:
        return svc.network(neuron_ids, src)
    except ODataError as exc:
        raise HTTPException(status_code=502, detail=f"Viking query failed: {exc}") from exc


# -- export ----------------------------------------------------------------
def _geometry_options(
    method: str = "cylinder",
    smooth_iterations: int = Query(1, ge=0, le=50),
    reduce_fraction: float = Query(1.0, gt=0, le=1),
    pathway: str = Query("original",
                         pattern="^(original|adaptive_taubin|voxel|voxel2|wrap|convolution|ultraliser)$"),
    post_order: str = Query("smooth_first", pattern="^(smooth_first|decimate_first)$"),
    voxel_detail: float = Query(1.0, gt=0.2, le=3),
    voxel_smooth_um: float = Query(0.5, ge=0, le=2),
    tie_um: float = Query(20.0, ge=0, le=100),
    ring_arc_um: float = Query(0.35, ge=0.1, le=1.5),
    wrap_spacing_um: float = Query(0.4, ge=0.15, le=2.0),
    wrap_blend: float = Query(2.0, ge=1.2, le=6.0),
    wrap_reject_sigma: float = Query(2.0, ge=0.5, le=6.0),
    wrap_passes: int = Query(2, ge=0, le=6),
    voxel2_um: float = Query(0.0, ge=0, le=0.3),
    voxel2_faces: int = Query(400_000, ge=0, le=5_000_000),
    voxel2_blend_um: float = Query(0.0, ge=0, le=1.0),
    exact_tube_deg: float = Query(0.0, ge=0, le=60),
    conv_support: float = Query(2.0, ge=1.2, le=4.0),
    ultra_soma: str = Query("discs", pattern="^(discs|sphere)$"),
    ultra_smooth: int = Query(15, ge=0, le=100),
    ultra_lambda: float = Query(0.5, ge=0, le=1.0),
    ultra_inflate: float = Query(0.52, ge=0, le=1.0),
    meshlab: str = Query("none", pattern="^(none|isotropic|qem|poisson)$"),
    meshlab_edge_um: float = Query(0.25, ge=0.05, le=2.0),
    meshlab_fraction: float = Query(0.25, ge=0.02, le=1.0),
    meshlab_depth: int = Query(9, ge=5, le=11),
    pixel_size_um: float = Query(0.05, gt=0, le=1),
    curve_avg_discs: float = Query(0.0, ge=0, le=3),
    curve_avg_paths: float = Query(0.0, ge=0, le=2),
    curve_links_um: float = Query(0.0, ge=0, le=20),
    curve_bridge_um: float = Query(1.0, ge=0, le=10),
    curve_link_bridge_um: float = Query(5.0, ge=0, le=50),
    curve_tie_um: float = Query(0.0, ge=0, le=20),
    surface_smooth_um: float = Query(0.0, ge=0, le=2),
) -> dict:
    """The full mesh-geometry option set, for exports that must match the
    on-screen mesh instead of silently rebuilding with the defaults.

    ``pixel_size_um`` is the closed-curve raster: without it a Closed curve or
    Hybrid cell shown at 0.25 um was exported at the 0.05 um default, a
    different (and about twenty times larger) mesh than the one on screen."""
    return {
        "method": method, "smooth_iterations": smooth_iterations,
        "reduce_fraction": reduce_fraction, "pathway": pathway,
        "post_order": post_order, "voxel_detail": voxel_detail,
        "voxel_smooth_um": voxel_smooth_um, "tie_um": tie_um,
        "ring_arc_um": ring_arc_um,
        "wrap_spacing_um": wrap_spacing_um, "wrap_blend": wrap_blend,
        "wrap_reject_sigma": wrap_reject_sigma, "wrap_passes": wrap_passes,
        "voxel2_um": voxel2_um, "voxel2_faces": voxel2_faces,
        "voxel2_blend_um": voxel2_blend_um, "exact_tube_deg": exact_tube_deg,
        "conv_support": conv_support,
        "ultra_soma": ultra_soma, "ultra_smooth": ultra_smooth,
        "ultra_lambda": ultra_lambda, "ultra_inflate": ultra_inflate,
        "meshlab": meshlab, "meshlab_edge_um": meshlab_edge_um,
        "meshlab_fraction": meshlab_fraction, "meshlab_depth": meshlab_depth,
        "pixel_size_um": pixel_size_um,
        "curve_avg_discs": curve_avg_discs, "curve_avg_paths": curve_avg_paths,
        "curve_links_um": curve_links_um, "curve_bridge_um": curve_bridge_um,
        "curve_link_bridge_um": curve_link_bridge_um, "curve_tie_um": curve_tie_um,
        "surface_smooth_um": surface_smooth_um,
    }


def _export_clip(
    clip_ipl: float | None = Query(
        None, description="Cut the exported mesh at this stratification depth "
                          "(0 = INL border, 1 = GCL border), as the viewer's Clip IPL does"),
    keep_above: bool = Query(True, description="Which side of the cut to keep"),
    clip_z: float | None = Query(None, description="Cut the exported mesh at this Z, in microns"),
    ipl_method: str = Query("column", pattern="^(normal|column)$",
                            description="How the IPL clip measures depth: along Z (column, "
                                        "MATLAB's) or along the layers' normals (normal)"),
) -> dict:
    """The viewer's clip, so an export of a clipped render is the surface on screen.

    A fifth declaration of four schema fields, beside the four the schema lists:
    ``render_schema`` marks ``clip_ipl``, ``keep_above``, ``clip_z`` and
    ``ipl_method`` as the mesh route's (``wire=('mesh',)``), and the export
    routes take the same four here, with ``_geometry_options`` carrying only the
    geometry.  The export link the browser builds (``exportQuery``) adds
    ``clip_ipl``, ``keep_above`` and ``ipl_method`` by hand.
    ``tests/unit/test_web.py`` holds this to the mesh route's type, default and
    bounds, and ``tests/js/test_exports.mjs`` holds the link's names to the schema's.
    """
    return {"clip_z": clip_z, "clip_ipl": clip_ipl, "keep_above": keep_above, "ipl_method": ipl_method}


#: Formats the wireframe exporter can express edges in.  SWC is the
#: centerline skeleton -- the cell's own wireframe -- rather than mesh edges.
WIREFRAME_FORMATS = ("obj", "ply", "stl", "swc",
                     "csv", "tsv", "npy", "npz", "h5")
WIREFRAME_MEDIA_TYPES = {
    "csv": "text/csv",
    "tsv": "text/tab-separated-values",
    "npy": "application/octet-stream",
    "npz": "application/zip",
    "h5": "application/x-hdf5",
}


@router.get("/neurons/{source}/{neuron_id}/export/{fmt}")
def export_neuron(request: Request, source: str, neuron_id: int, fmt: str,
                  geometry: dict = Depends(_geometry_options),
                  clip: dict = Depends(_export_clip),
                  wireframe: bool = Query(False),
                  wireframe_thickness_um: float = Query(0.05, gt=0, le=1),
                  swc_branch_types: bool = Query(
                      True,
                      description="SWC only: type branch points 5 and tips 6 as the "
                                  "MATLAB exporter did (default); false types every "
                                  "neurite node dendrite (3)"),
                  swc_relax: float | None = Query(
                      None, ge=0, le=1,
                      description="SWC only: write each node moved toward its neighbors, "
                                  "never outside this fraction of its own annotation "
                                  "(0 = Viking's positions, 1 = anywhere inside); the header "
                                  "records it"),
                  swc_relax_bound: str = Query(
                      "outline", pattern="^(outline|radius)$",
                      description="What holds a relaxed node: its traced outline (its "
                                  "circle where it has none), or its stored radius"),
                  swc_lamellae: bool = Query(
                      False,
                      description="SWC only: write each lamella (a profile at least twice "
                                  "as long as wide, or a stack of them) as the chain along "
                                  "it, in place of its annotations; not with swc_relax"),
                  swc_sampling: str = Query(
                      "annotations", pattern="^(annotations|sphere)$",
                      description="SWC only: one row per annotation, or the rows Bradley & "
                                  "Withers's minimal sphere network keeps"),
                  swc_radius: str = Query(
                      "stored", pattern="^(stored|modal)$",
                      description="SWC only: what R holds for a closed curve: Viking's "
                                  "stored radius, or its modal radius (the stored one then "
                                  "follows as R_STORED rows)")):
    svc = service(request)
    src = _source(source)
    fmt = fmt.lower()
    if fmt == "hdf5":
        fmt = "h5"
    if wireframe:
        if fmt not in WIREFRAME_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"Wireframe export supports {WIREFRAME_FORMATS}, not {fmt!r}",
            )
    elif fmt not in EXPORT_MEDIA_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {fmt!r}; expected one of {sorted(EXPORT_MEDIA_TYPES)}",
        )
    neuron = _neuron_or_404(svc, neuron_id, src)

    # The volume travels with the ID (ground rule 11), as in the linked-neurons
    # files and the bulk archive: c943 is another cell in every volume.
    stem = f"c{neuron_id}-{src}-wireframe" if wireframe else f"c{neuron_id}-{src}"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{stem}.{fmt}"
        if wireframe and fmt != "swc":
            mesh = _mesh_object_or_400(svc, neuron_id, src, geometry, clip)
            if mesh.is_empty:
                raise HTTPException(status_code=400,
                                    detail="Neuron produced an empty mesh")
            _write_wireframe(mesh, fmt, path, thickness_um=wireframe_thickness_um,
                             name=f"c{neuron_id}")
        elif fmt == "swc":
            from sbfsem.export.swc import write_swc

            try:
                write_swc(neuron, path, branch_types=swc_branch_types,
                          relax=swc_relax, relax_bound=swc_relax_bound,
                          lamellae=swc_lamellae, sampling=swc_sampling, radius=swc_radius)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        elif fmt == "json":
            from sbfsem.export.json_v2 import write_neuron_json

            write_neuron_json(neuron, path, indent=1)
        else:
            mesh = _mesh_object_or_400(svc, neuron_id, src, geometry, clip)
            if mesh.is_empty:
                raise HTTPException(status_code=400, detail="Neuron produced an empty mesh")
            if fmt == "dae":
                from sbfsem.export.collada import write_collada

                write_collada({f"c{neuron_id}": mesh}, path)
            else:
                from sbfsem.export import mesh_formats

                writer = {
                    "stl": mesh_formats.write_stl,
                    "obj": mesh_formats.write_obj,
                    "ply": mesh_formats.write_ply,
                    "glb": mesh_formats.write_gltf,
                }[fmt]
                writer(mesh, path)
        data = path.read_bytes()

    return Response(
        content=data,
        media_type={**EXPORT_MEDIA_TYPES, **WIREFRAME_MEDIA_TYPES}[fmt],
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'},
    )


def _write_wireframe(wire, fmt: str, path: Path, *, thickness_um: float, name: str,
                     notes: dict | None = None) -> None:
    """A mesh's edges, or ``(S, 2, 3)`` line segments, in one wireframe format.

    ``notes`` go where the format has room for them: OBJ and PLY comments,
    HDF5 attributes.
    """
    from sbfsem.export import mesh_formats

    if fmt == "obj":
        mesh_formats.write_wireframe_obj(wire, path, notes=notes)
    elif fmt == "ply":
        mesh_formats.write_wireframe_ply(wire, path, notes=notes)
    elif fmt == "stl":
        mesh_formats.write_wireframe_stl(wire, path, thickness_um=thickness_um)
    elif fmt in ("csv", "tsv"):
        mesh_formats.write_wireframe_table(
            wire, path, delimiter="," if fmt == "csv" else "\t")
    elif fmt == "npy":
        mesh_formats.write_wireframe_npy(wire, path)
    elif fmt == "npz":
        mesh_formats.write_wireframe_npz(wire, path)
    else:
        mesh_formats.write_wireframe_hdf5(wire, path, name=name, notes=notes)


#: Formats the annotation wireframe exports in: the mesh wireframe's, less
#: SWC, a tree of nodes with no place for a ring.
ANNOTATION_WIRE_FORMATS = tuple(f for f in WIREFRAME_FORMATS if f != "swc")


@router.get("/neurons/{source}/{neuron_id}/annotation-wire/export/{fmt}")
def export_annotation_wire(
    request: Request, source: str, neuron_id: int, fmt: str,
    options: dict = Depends(_annotation_wire_options),
    wireframe_thickness_um: float = Query(0.05, gt=0, le=1),
):
    """The annotation wireframe as a file: what the viewer draws with these settings.

    Rings, the lines along the chains and the ties, as line segments in
    microns; the settings are recorded where the format has room.
    """
    svc = service(request)
    src = _source(source)
    fmt = fmt.lower()
    if fmt == "hdf5":
        fmt = "h5"
    if fmt not in ANNOTATION_WIRE_FORMATS:
        raise HTTPException(
            status_code=400,
            detail=f"Annotation wireframe export supports {ANNOTATION_WIRE_FORMATS}, "
                   f"not {fmt!r}",
        )
    _neuron_or_404(svc, neuron_id, src)
    segments = svc.annotation_wire_segments(neuron_id, src, **options)
    if not len(segments):
        raise HTTPException(status_code=400,
                            detail="Neuron produced an empty annotation wireframe")
    stem = f"c{neuron_id}-{src}-annotation-wire"
    notes = {"source": src, "neuron_id": neuron_id, "units": "micron", **options}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"{stem}.{fmt}"
        _write_wireframe(segments, fmt, path, thickness_um=wireframe_thickness_um,
                         name=f"c{neuron_id}", notes=notes)
        data = path.read_bytes()
    return Response(
        content=data,
        media_type={**EXPORT_MEDIA_TYPES, **WIREFRAME_MEDIA_TYPES}[fmt],
        headers={"Content-Disposition": f'attachment; filename="{stem}.{fmt}"'},
    )


#: Structures in one archive.  Each is a Viking round trip when it is not
#: cached (RPC1 c192: 5 s), and the request holds a connection open meanwhile.
BULK_EXPORT_LIMIT = 200

#: Seconds an archive may spend starting structures.  nginx gives up on a
#: response at 300 s; past this the structures not yet reached are manifest
#: rows that say so, and the archive that was built is sent (a list of 200 cold
#: cells would otherwise end as a 504 with nothing).
BULK_EXPORT_BUDGET_S = 240.0


@router.get("/neurons/{source}/export-bulk/swc")
def export_swc_bulk(request: Request, source: str,
                    ids: str = Query(..., description="Structure IDs, comma or space separated"),
                    pattern: str = Query(
                        "c{id}", max_length=80,
                        description="File name without the extension; placeholders "
                                    "{id}, {volume}, {label}"),
                    swc_branch_types: bool = Query(
                        False,
                        description="Type branch points 5 and tips 6 as the MATLAB "
                                    "exporter did; false (default here) types every "
                                    "neurite node dendrite (3)"),
                    refresh: bool = Query(
                        False, description="Ask Viking again instead of the disk cache"),
                    swc_relax: float | None = Query(
                        None, ge=0, le=1,
                        description="Write relaxed positions (see the single export)"),
                    swc_relax_bound: str = Query("outline", pattern="^(outline|radius)$"),
                    bracket: bool = Query(
                        False,
                        description="Add length_floor_um and length_floor_by_radius_um to "
                                    "the manifest (seconds per large cell)"),
                    swc_lamellae: bool = Query(False, description="As the single export"),
                    swc_sampling: str = Query("annotations", pattern="^(annotations|sphere)$"),
                    swc_radius: str = Query("stored", pattern="^(stored|modal)$"),
                    live_registration: bool | None = Query(
                        None,
                        description="Draw every cell through its volume's current section "
                                    "transforms (true) or at its stored positions (false); "
                                    "absent: the server's default"),
                    section_corrections: str | None = Query(
                        None,
                        description="Viking's section correction for every cell: off, "
                                    "neighbor, curvefit or all; absent: the server's default")):
    """A zip of one SWC per structure plus ``manifest.csv``: a population in
    one request, every file carrying its own provenance header, every cell
    drawn the same way."""
    svc = service(request)
    src = _source(source)
    try:
        wanted = [int(token) for token in re.split(r"[\s,;]+", ids.strip()) if token]
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ids must be integers") from exc
    if not wanted:
        raise HTTPException(status_code=400, detail="No structure IDs given")
    if len(wanted) > BULK_EXPORT_LIMIT:
        raise HTTPException(
            status_code=400,
            detail=f"At most {BULK_EXPORT_LIMIT} structures per archive, not {len(wanted)}")
    try:
        mode = None if section_corrections is None else correction_mode(section_corrections)
        data = svc.swc_archive(wanted, src, pattern=pattern,
                               branch_types=swc_branch_types, refresh=refresh,
                               relax=swc_relax, relax_bound=swc_relax_bound, bracket=bracket,
                               lamellae=swc_lamellae, sampling=swc_sampling, radius=swc_radius,
                               section_corrections=mode, live_registration=live_registration,
                               time_budget_s=BULK_EXPORT_BUDGET_S)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{src}-swc.zip"',
            # A partial archive (the time budget ran out; a structure failed) is
            # a 200, and a client reads the response, not the zip: these say how
            # many structures it holds, so it can tell the person.
            "X-Archive-Asked": str(data.asked),
            "X-Archive-Written": str(data.written),
            "X-Archive-Failed": str(data.failed),
            "X-Archive-Not-Attempted": str(data.not_attempted),
        },
    )


@router.get("/neurons/{source}/{neuron_id}/mesh.glb")
def mesh_glb(request: Request, source: str, neuron_id: int,
             geometry: dict = Depends(_geometry_options),
             clip: dict = Depends(_export_clip)):
    """The mesh as binary glTF, for streaming straight into a WebGL loader."""
    from sbfsem.export.mesh_formats import mesh_to_glb_bytes

    svc = service(request)
    src = _source(source)
    _neuron_or_404(svc, neuron_id, src)
    mesh = _mesh_object_or_400(svc, neuron_id, src, geometry, clip)
    if mesh.is_empty:
        raise HTTPException(status_code=400, detail="Neuron produced an empty mesh")
    return Response(
        content=mesh_to_glb_bytes(mesh, f"c{neuron_id}"),
        media_type="model/gltf-binary",
    )

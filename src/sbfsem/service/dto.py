"""Pydantic DTOs crossing the frontend boundary.

Nothing here is a pandas frame, a numpy array or a VTK object: every type is
JSON-serializable, so the same :class:`~sbfsem.service.facade.SbfsemService` call
works in-process and over HTTP.  Meshes cross as typed buffers (base64 GLB, or
plain float/int lists), never as backend objects.
"""

from __future__ import annotations

import math
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class VolumeInfoDTO(BaseModel):
    #: Wire identifier: API paths, cache filenames and exported JSON use this.
    name: str
    #: What the interface shows.  Never use it as a key.
    display_name: str
    abbrev: str | None = None
    animal: str
    is_odata: bool
    has_boundary: bool
    has_cones: bool
    has_custom_transform: bool
    n_sections: int | None = None
    volume_scale: list[float]
    service_root: str | None = None
    #: The Viking Identity Server's name for this volume (``RC2``, ``NM``):
    #: what "Open in Viking" and ``/open`` links speak.  ``None`` when the
    #: identity server does not know the volume (the bundled demo).
    viking_name: str | None = None
    #: Whether the volume's current section transforms (the stos files its
    #: VikingXML lists) are read here, so a cell can be drawn where Viking
    #: draws it now (:mod:`sbfsem.io.registration`).  ``False``: its cells are
    #: always at their stored positions, and the viewer does not offer the
    #: switch.  Says nothing about Viking's section correction, which every
    #: Viking volume may have.
    has_transforms: bool = False
    #: The lab whose volume it is (``Neitz``, ``Marc``); ``None`` for the demo.
    lab: str | None = None
    #: The section correction a load of this volume gets when it names none
    #: (``off``, ``neighbor``, ``curvefit`` or ``all``): the web app's is
    #: ``all`` (Field + fit) for the Neitz lab's volumes, ``curvefit`` for the others.
    default_correction: str = "off"
    #: Whether a load that does not say is drawn through the volume's current
    #: section transforms (where it has them: ``has_transforms``).
    default_registration: bool = False


class SynapseTypeCountDTO(BaseModel):
    local_name: str
    viking_name: str
    count: int
    color: list[float]
    #: Of ``count``, the postsynapses typed from their linked partner because
    #: their Viking tags do not say (see ``untagged_psd``).
    inferred: int = 0
    #: Of ``count``, the postsynapses assumed conventional for the same reason.
    assumed: int = 0


class SectionCorrectionDTO(BaseModel):
    """Viking's section correction for one loaded cell (:mod:`sbfsem.io.corrections`).

    ``requested`` without ``applied`` says why in ``note``: no set published
    for the volume, or the service not answering.  A correction applied from
    offsets kept on disk while the service was down is ``cached`` and says so
    in ``note`` too.
    """

    requested: bool = False
    applied: bool = False
    note: str = ""
    #: What was asked of the service: ``neighbor`` (the residual field),
    #: ``curvefit`` (a fit along each traced process) or ``all`` (both).
    mode: str | None = None
    volume_name: str | None = None
    stos_group: str | None = None
    built_utc: datetime | None = None
    annotation_watermark: datetime | None = None
    service: str | None = None
    #: The corrections service's version the answer was keyed on (``curvefit``
    #: and ``all``; ``""`` when the service did not say).
    service_version: str | None = None
    cached: bool = False
    #: Annotations sampled (the cell's and its child structures'), sections
    #: they are on, those on a published section, those the field supports
    #: (the only ones moved), and those that moved at all.
    n_annotations: int = 0
    n_sections: int = 0
    n_found: int = 0
    n_trusted: int = 0
    n_moved: int = 0
    #: How far the moved annotations went, um.
    median_um: float = 0.0
    p95_um: float = 0.0
    max_um: float = 0.0


class LiveRegistrationDTO(BaseModel):
    """Viking's current section transforms for one loaded cell (:mod:`sbfsem.io.registration`).

    ``requested`` without ``applied`` says why in ``note`` (the volume's
    transforms are not read here, or its files could not be fetched): the cell
    is then at its stored positions.
    """

    requested: bool = False
    applied: bool = False
    note: str = ""
    vikingxml: str | None = None
    stos_group: str | None = None
    xml_last_modified: str | None = None
    #: Annotations, their sections, those inside a transform, and those the
    #: current transforms put more than 10 nm from their stored position.
    n_annotations: int = 0
    n_sections: int = 0
    n_mapped: int = 0
    n_moved: int = 0
    n_sections_moved: int = 0
    #: The sections where something moved, as ranges ("937-1258, 1260-1288").
    sections_moved: str = ""
    median_um: float = 0.0
    p95_um: float = 0.0
    max_um: float = 0.0


class ManualMovesDTO(BaseModel):
    """A person's own moves applied to this copy of the cell (``sbfsem.core.moves``).

    Never Viking's: the page that made them sends them with each load, and the
    copy they were applied to is named by their ``token`` (in ``copy_tag``).
    """

    applied: bool = False
    token: str | None = None
    #: Moves sent, the ones applied (the cell's own annotations), the ones
    #: naming an annotation the cell does not have (the first 20 of them), and
    #: the ones that put an annotation on another section.
    n_moves: int = 0
    n_moved: int = 0
    n_unknown: int = 0
    unknown: list[int] = []
    n_z: int = 0
    #: The longest move in X and Y, um.
    max_um: float = 0.0


class NeuronSummaryDTO(BaseModel):
    id: int
    source: str
    label: str = ""
    type_id: int | None = None
    n_nodes: int
    n_edges: int
    n_synapses: int = 0
    has_geometry: bool = False
    #: The cell's own annotations by kind: discs (TypeCode 1) and traced
    #: closed-curve outlines (TypeCode 6).  Both non-zero means a mixed
    #: structure, which only the hybrid technique renders in full.
    n_disc_annotations: int = 0
    n_curve_annotations: int = 0
    soma_radius_um: float | None = None
    soma_xyz_um: list[float] | None = None
    bounding_box_um: list[float] | None = None
    last_modified: datetime | None = None
    #: Newest annotation edit among the nodes this summary was built from;
    #: what a cached render is stamped with.
    data_stamp: datetime | None = None
    notes: str = ""
    #: Whether Viking's section correction was asked for and applied.
    #: ``None`` when it was not asked for.
    section_correction: SectionCorrectionDTO | None = None
    #: Whether the volume's current section transforms were asked for and
    #: applied.  ``None`` when not asked for (stored positions).
    live_registration: LiveRegistrationDTO | None = None
    #: A person's own moves after the correction; ``None`` when there are none.
    manual_moves: ManualMovesDTO | None = None
    #: Which copy of the cell this is: the server holds one per setting in use
    #: (correction mode, current transforms or not), so tabs with different
    #: settings never change each other's cell.  The page sends it back as
    #: ``?copy=`` on every later request for the cell.
    copy_tag: str | None = None
    #: Which moves were applied to the annotations: the tokens of the current
    #: section transforms and of the section correction, ``None`` at the stored
    #: positions.  ``copy_tag`` says what was asked for; a correction asked for
    #: while the service was down leaves the cell uncorrected under the same
    #: tag, so what is cached from the positions is keyed on this as well.
    positions: str | None = None


class NodeDTO(BaseModel):
    id: int
    parent_id: int
    xyz_um: list[float]
    radius_um: float
    z_section: int
    off_edge: bool = False
    terminal: bool = False
    unique: bool = False
    type_code: int = 1
    #: How far Viking's section correction moved this annotation in X and Y,
    #: um; ``None`` when it did not move.  ``xyz_um`` minus this is where the
    #: volume's current section transforms put it, which is where Viking draws
    #: it and what Goto Location takes.  That is also its stored position
    #: unless ``live_shift_um`` says the transforms moved it too.
    shift_um: list[float] | None = None
    #: How far the volume's current section transforms moved this annotation
    #: from its stored position (``sbfsem.io.registration``), um, in X and Y;
    #: ``None`` when the cell was not drawn through them or this annotation
    #: did not move.  Rounded to 1e-6 um.  The three stages of one copy of the cell
    #: (Standard transform; a cell loaded with transform "None" has no moves):
    #: stored = ``xyz_um`` - ``live_shift_um`` - ``shift_um``;
    #: registered = ``xyz_um`` - ``shift_um``; corrected = ``xyz_um`` (a
    #: ``None`` counts as zero).  Z does not move.
    live_shift_um: list[float] | None = None
    #: A person's own move of this annotation after the correction
    #: (``sbfsem.core.moves``): X, Y and Z in um; ``None`` when it was not
    #: moved by hand.  The corrected stage is then ``xyz_um`` minus this, and
    #: ``z_section`` is the section it was moved to: Viking's is
    #: ``z_section - manual_dz``.
    manual_shift_um: list[float] | None = None
    manual_dz: int | None = None


class LocationDTO(BaseModel):
    """Where one Viking annotation (a Location) is."""

    id: int
    #: The structure the location belongs to (a synapse's own id for a
    #: child-structure annotation).
    parent_id: int
    #: The cell it was found in when loaded, else ``parent_id``.
    structure_id: int
    xyz_um: list[float]
    #: Viking pixel X, Y and section Z, as Goto Location wants them.
    viking: list[int]
    radius_um: float
    #: Whether a loaded cell holds it (else Viking was asked).
    loaded: bool


class EdgeDTO(BaseModel):
    structure_id: int
    a: int
    b: int


class FreshnessDTO(BaseModel):
    """Whether Viking has edited a cell since its data was loaded.

    ``loaded`` and ``live`` are UTC.  ``stale`` is either kind of change:
    ``surface_stale`` when the cell's own annotations differ (a newer edit or
    a different count; its surface would change), ``children_stale`` when only
    its child structures do (synapse markers).  The counts are the cell's own
    annotations, loaded and in Viking now.
    """

    loaded: datetime | None = None
    live: datetime | None = None
    stale: bool = False
    surface_stale: bool = False
    children_stale: bool = False
    loaded_count: int | None = None
    live_count: int | None = None


class NeuronDetailDTO(BaseModel):
    summary: NeuronSummaryDTO
    synapse_counts: list[SynapseTypeCountDTO] = Field(default_factory=list)
    volume: VolumeInfoDTO
    #: Render settings attached to this cell on the server, if any.  Filled by
    #: the web layer (the facade knows nothing about the render store).
    attached_render: dict | None = None
    #: Filled by the web layer on a load when Viking could be asked: whether
    #: the cell has been edited since the data now in memory was fetched.
    freshness: FreshnessDTO | None = None


class MeshDTO(BaseModel):
    """A triangle mesh as flat buffers, ready for a WebGL BufferGeometry."""

    name: str
    n_vertices: int
    n_faces: int
    vertices: list[float] = Field(default_factory=list)
    faces: list[int] = Field(default_factory=list)
    vertex_data: list[float] | None = None
    bounding_box_um: list[float] = Field(default_factory=list)
    surface_area_um2: float = 0.0
    volume_um3: float = 0.0
    #: A short id of this exact mesh (its cache key): a measurement answers
    #: with the same id, so a client holding the mesh can attach the numbers
    #: to it without the server sending the geometry a second time.  ``None``
    #: for a mesh that is not the cached render (one cut by ``clip_z`` or ``clip_ipl``).
    mesh_id: str | None = None
    #: The whole-cell grid's voxel the Voxel remesh, Convolution and Ultraliser
    #: pathways actually contoured at (``voxel2_um`` is what was asked: 0 sizes
    #: it from the annotations), whether a budget made it coarser than asked,
    #: and why (``"floor"``, ``"faces"``: the raw-triangle budget, ``"work"``:
    #: the evaluation budget).  ``None`` for every other technique, and for a
    #: surface stored before 2026-10-16 the reason too.  The page says when a
    #: Fine voxel that was typed came out coarser.
    voxel2_um: float | None = None
    voxel2_asked_um: float | None = None
    voxel2_coarsened: bool | None = None
    voxel2_reason: str | None = None


class DeviationDTO(BaseModel):
    """Absolute deviation of one rendering from the cell's own annotations.

    The annotations (discs, plus any closed-curve outlines) are treated as
    ground truth.  ``surface_um`` is the mean distance from the rendered
    surface to the nearest annotation (surface with no annotated support —
    bulges, sweeps through unannotated tissue — scores here);
    ``annotation_um`` is the mean distance from every annotation's rim to the
    surface (annotations the render failed to reach score here).  Both are
    absolute values, so errors never cancel.  ``excess_volume_um3`` is
    ``surface_um`` times the surface area: a first-order estimate of the
    volume the render claims beyond any annotation's support.
    """

    mean_um: float
    surface_um: float
    annotation_um: float
    p95_um: float
    max_um: float
    excess_volume_um3: float
    sample_spacing_um: float
    n_discs: int
    n_curves: int
    #: ``surface_um`` over the surface that can be seen: a sample is hidden when
    #: a hair outside it along its normal is still enclosed by the mesh itself
    #: (a tube inside a soma, one piece inside another).  Hidden surface scores
    #: near 0 in ``surface_um`` and dilutes its mean, so a render assembled from
    #: interpenetrating pieces looks better there than it is.  ``null`` when
    #: every sample is hidden, and in a result made before these three existed.
    exterior_um: float | None = None
    #: The share of the surface area that is hidden in that sense.
    hidden_fraction: float = 0.0
    #: ``annotation_um`` over the rims the render does not enclose: what is left
    #: once a rim buried in the render (traced on through a soma, or a body made
    #: too fat) is set aside is the render's thinness.  ``null`` when no rim is
    #: exterior.
    annotation_exterior_um: float | None = None


class AreaStatsDTO(BaseModel):
    """A signed field's statistics over the *surface* of the mesh it lies on:
    each vertex counts for the area it stands for (a third of its faces), so a
    region meshed ten times as finely weighs the same.  Microns.  ``p98_abs_um``
    is the color scale these figures suggest; ``max_abs_um`` is over the
    vertices some face uses."""

    mean_abs_um: float
    rms_um: float
    p95_abs_um: float
    p98_abs_um: float
    max_abs_um: float
    mean_signed_um: float


class MeshDiffDTO(BaseModel):
    """Mesh A colored by its signed distance to mesh B.

    ``mesh.vertex_data`` carries the per-vertex signed distance in microns
    (positive = outside B).  The stats say how big the disagreement is and
    ``sample_spacing_um`` says how finely it was measured — differences below
    that spacing are sampling noise, not geometry.  ``deviation_a`` and
    ``deviation_b`` hold each rendering's absolute deviation from the cell's
    annotations (None when the cell has no annotations to measure against).
    ``sign_by`` says what decided the sign: ``normal`` (B's outward normal at
    the nearest surface sample) or ``enclosure`` (whether B's solid encloses
    the vertex: a ray count over B with its open ends closed, and B's winding
    number where closing an end would invent a sheet).
    """

    mesh: MeshDTO
    mean_abs_um: float
    rms_um: float
    p95_abs_um: float
    max_abs_um: float
    mean_signed_um: float
    scale_um: float
    sample_spacing_um: float
    n_samples: int
    deviation_a: DeviationDTO | None = None
    deviation_b: DeviationDTO | None = None
    sign_by: str = "normal"


class DeviationMapDTO(BaseModel):
    """One rendering held to the cell's own annotations, point by point.

    ``mesh.vertex_data`` is each vertex's distance to the nearest annotation
    in microns; ``rim_xyz`` (flat x, y, z) and ``rim_um`` are points along the
    annotations' rims with each one's distance to the rendered surface, thinned
    to every ``rim_stride``-th of ``n_rims`` for the browser.  ``scale_um`` is
    the 98th percentile of both pooled, a color scale; ``deviation`` holds the
    averages the two sets produce.  All distances are absolute.
    """

    mesh: MeshDTO
    rim_xyz: list[float] = Field(default_factory=list)
    rim_um: list[float] = Field(default_factory=list)
    rim_stride: int = 1
    n_rims: int = 0
    scale_um: float
    deviation: DeviationDTO | None = None


class DeviationHistogramDTO(BaseModel):
    """The surface's error in 40 equal bins: ``edges`` (41 values, microns)
    bound them, ``area_share`` is the share of the surface area in each, and
    ``tail_share`` the share beyond the last edge."""

    edges: list[float] = Field(default_factory=list)
    area_share: list[float] = Field(default_factory=list)
    tail_share: float = 0.0


class DeviationSurfaceDTO(BaseModel):
    """The error of the render's surface, weighted by area (a region meshed
    ten times as finely counts the same), in microns.  ``quantiles`` holds the
    0th to 100th percentile (101 values); ``floor_um`` is the error a perfect
    render still shows between sections (half the median gap between annotated
    sections); ``area_um2`` is the surface area and ``excess_um3`` its area
    times error summed over the vertices, the estimate of the volume the render
    claims beyond any annotation's support that the spots add up to."""

    area_um2: float
    mean_um: float
    excess_um3: float
    quantiles: list[float] = Field(default_factory=list)
    hist: DeviationHistogramDTO = Field(default_factory=DeviationHistogramDTO)
    floor_um: float = 0.0


class DeviationAnnotationsDTO(BaseModel):
    """One entry per annotation in every list, in the order the cell's
    annotations are measured: its disc annotations, then its closed-curve
    outlines.  ``ids`` are Location IDs (numbered per volume) and ``sections``
    their section numbers; ``kind`` is 0 for a disc and 1 for an outline;
    ``xyz`` (flat x, y, z, microns) is the disc's center or the outline's
    centroid and ``radius`` (microns) the disc's radius or the outline's
    equivalent.  ``area_share`` is the share of the surface area whose nearest
    annotation this is, with the ``surface_mean``/``surface_max`` error over it;
    ``rim_mean``/``rim_max`` are the distances from this annotation's rim to the
    surface; ``rim_enclosed_share`` is the share of its displayed rim points the
    render encloses (null where none was tested)."""

    ids: list[int] = Field(default_factory=list)
    sections: list[int] = Field(default_factory=list)
    kind: list[int] = Field(default_factory=list)
    xyz: list[float] = Field(default_factory=list)
    radius: list[float] = Field(default_factory=list)
    area_share: list[float] = Field(default_factory=list)
    surface_mean: list[float] = Field(default_factory=list)
    surface_max: list[float] = Field(default_factory=list)
    rim_mean: list[float] = Field(default_factory=list)
    rim_max: list[float] = Field(default_factory=list)
    rim_enclosed_share: list[float | None] = Field(default_factory=list)


class DeviationZProfileDTO(BaseModel):
    """The error along the cell in bins of ``step`` microns from ``z0``: the
    surface ``area`` in each bin (square microns), its area-weighted ``mean``
    and 95th percentile ``p95`` error, the ``rim_mean`` distance of the
    annotation rims there, and how many annotations sit there."""

    z0: float
    step: float
    area: list[float] = Field(default_factory=list)
    mean: list[float] = Field(default_factory=list)
    p95: list[float] = Field(default_factory=list)
    rim_mean: list[float] = Field(default_factory=list)
    n_annotations: list[int] = Field(default_factory=list)


class DeviationSpotDTO(BaseModel):
    """A place worth looking at: the surface within 2 microns of ``xyz`` (a
    surface vertex, microns) carries ``excess_um3`` of the render's excess
    volume (``share`` of all of it) at ``mean`` microns of error on average and
    ``max`` at worst.  ``annotation_id`` (a Location ID, numbered per volume) and
    ``section`` name the annotation that accounts for most of it."""

    xyz: list[float]
    section: int | None = None
    annotation_id: int | None = None
    excess_um3: float
    share: float
    mean: float
    max: float


class DeviationBiasDTO(BaseModel):
    """Which way the render is wrong, from the rim points it was tested on.  A
    rim point the render encloses lies inside the body: the render is fatter
    than that annotation there; one it does not enclose lies outside: the
    render stops short.  ``signed_mean_um`` counts the first as positive and the
    second as negative, so positive means the render claims more than the
    annotations support.  ``n`` rim points were tested."""

    n: int
    enclosed_share: float
    enclosed_mean_um: float | None = None
    outside_mean_um: float | None = None
    signed_mean_um: float = 0.0


class DeviationResultDTO(BaseModel):
    """:class:`DeviationMapDTO` without the geometry, for a job.

    ``vertex_um`` is every vertex's distance to the nearest annotation, in the
    vertex order of the mesh with this ``mesh_id`` and ``n_vertices`` -- which
    the client already holds.  Everything else is as in
    :class:`DeviationMapDTO`, plus how the error arises (all of it empty or
    null where a result was made without it, so a client that does not know
    them reads the result as before): ``axial_um`` is each vertex's gap along Z
    to its nearest annotation (the sideways part is
    ``sqrt(vertex_um**2 - axial_um**2)``); ``rim_enclosed`` is 1 for each
    displayed rim point the render encloses and 0 for each it does not;
    ``surface``, ``annotations``, ``z_profile``, ``spots`` and ``bias`` are the
    error by area, by annotation, along Z, at its worst places, and by side.
    """

    mesh_id: str | None = None
    n_vertices: int
    n_faces: int
    vertex_um: list[float] = Field(default_factory=list)
    rim_xyz: list[float] = Field(default_factory=list)
    rim_um: list[float] = Field(default_factory=list)
    rim_stride: int = 1
    n_rims: int = 0
    scale_um: float
    deviation: DeviationDTO | None = None
    axial_um: list[float] = Field(default_factory=list)
    rim_enclosed: list[int] = Field(default_factory=list)
    surface: DeviationSurfaceDTO | None = None
    annotations: DeviationAnnotationsDTO | None = None
    z_profile: DeviationZProfileDTO | None = None
    spots: list[DeviationSpotDTO] = Field(default_factory=list)
    bias: DeviationBiasDTO | None = None


class DiffResultDTO(BaseModel):
    """:class:`MeshDiffDTO` without the geometry, for a job.

    ``distance_um`` is the signed distance of every vertex of mesh A to mesh B
    (positive = outside B), in the vertex order of the mesh with this
    ``mesh_id`` (mesh A's), which the client already holds.  The statistics
    above it count every vertex once; ``stats_area`` holds the same figures
    weighted by the area each vertex stands for.  ``sign_by`` says what decided
    the sign: ``normal`` (B's outward normal at the nearest surface sample,
    wrong where the nearest sample is on a piece buried in B) or ``enclosure``
    (whether B's solid encloses the vertex: a ray count over B with its open
    ends closed, and B's winding number where closing an end would invent a
    sheet).  Asked for ``both`` ways, the
    result also carries ``distance_b_um``: the signed distance of every vertex
    of mesh B to mesh A (positive = B outside A), in the vertex order of mesh B,
    with ``stats_area_b``.  ``mesh_id_b`` and ``n_vertices_b`` name mesh B in
    every result, so a client holding it can check it is the one compared.
    ``sign_overruled`` (and ``sign_overruled_b`` for the reverse field) counts
    the vertices the winding number of an open B put on the other side from the
    count over B with its ends capped: they lie under a fan that would have
    been a sheet B does not have.  0 for a closed B and by ``normal``.
    ``copy_a`` and ``copy_b`` name the copy of the cell each mesh was built from
    and held to (a summary's ``copy_tag``); they differ when the difference was
    asked across two copies, a surface before and after a setting that moves the
    annotations.
    """

    mesh_id: str | None = None
    n_vertices: int
    n_faces: int
    distance_um: list[float] = Field(default_factory=list)
    mean_abs_um: float
    rms_um: float
    p95_abs_um: float
    max_abs_um: float
    mean_signed_um: float
    scale_um: float
    sample_spacing_um: float
    n_samples: int
    deviation_a: DeviationDTO | None = None
    deviation_b: DeviationDTO | None = None
    sign_by: str = "normal"
    stats_area: AreaStatsDTO | None = None
    distance_b_um: list[float] = Field(default_factory=list)
    stats_area_b: AreaStatsDTO | None = None
    mesh_id_b: str | None = None
    n_vertices_b: int = 0
    sign_overruled: int = 0
    sign_overruled_b: int = 0
    copy_a: str | None = None
    copy_b: str | None = None


class SegmentDTO(BaseModel):
    location_ids: list[int]
    points_um: list[list[float]]
    radii_um: list[float]


class SkeletonDTO(BaseModel):
    neuron_id: int
    segments: list[SegmentDTO]
    soma_xyz_um: list[float] | None = None
    soma_radius_um: float | None = None


class AnnotationWireDTO(BaseModel):
    """The annotations as an averaged wireframe, in microns, as polylines.

    ``rings`` are closed, each ``[z, x0, y0, x1, y1, ...]`` in its section
    plane; ``lines`` run along the chains, each ``[x0, y0, z0, x1, y1, z1,
    ...]``; ``ties`` are flat segment pairs ``[x0, y0, z0, x1, y1, z1, ...]``
    from a branch's ring onto the annotation it leaves.  ``moved_um``
    describes how far the averaging moved each ring's outline.
    """

    neuron_id: int
    rings: list[list[float]] = Field(default_factory=list)
    lines: list[list[float]] = Field(default_factory=list)
    ties: list[float] = Field(default_factory=list)
    n_annotations: int = 0
    n_chains: int = 0
    n_ties: int = 0
    moved_um: StatsDTO | None = None


class StatsDTO(BaseModel):
    """Non-finite values become ``null``: ``NaN`` is not valid JSON."""

    n: int
    mean: float | None = None
    median: float | None = None
    sem: float | None = None
    std: float | None = None
    var: float | None = None
    min: float | None = None
    max: float | None = None

    @field_validator("mean", "median", "sem", "std", "var", "min", "max", mode="before")
    @classmethod
    def _finite_or_none(cls, value):
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None


class StratificationDTO(BaseModel):
    neuron_id: int
    label: str = ""
    counts: list[float]
    centers: list[float]
    edges: list[float]
    stats: StatsDTO
    mode: float | None = None
    n_omitted_nan: int = 0
    n_omitted_outlier: int = 0

    @field_validator("mode", mode="before")
    @classmethod
    def _finite_or_none(cls, value):
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None


class BinStatsDTO(BaseModel):
    """One bin of a binned profile.  Non-finite values become ``null``."""

    bin: int
    distance: float
    n: int
    mean: float | None = None
    sem: float | None = None
    std: float | None = None

    @field_validator("mean", "sem", "std", mode="before")
    @classmethod
    def _finite_or_none(cls, value):
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None


class DendriteDiameterDTO(BaseModel):
    neuron_id: int
    bin_width_um: float
    rows: list[BinStatsDTO] = Field(default_factory=list)


class HullDTO(BaseModel):
    points_um: list[list[float]]
    area_um2: float
    perimeter_um: float
    equivalent_diameter_um: float


class IERatioDTO(BaseModel):
    #: numInh / numExc, the documented I:E ratio.
    ratio: float | None = None
    #: numInh / (numInh + numExc), what the MATLAB ieRatio actually returned.
    inhibitory_fraction: float | None = None
    n_excitatory: int
    n_inhibitory: int
    #: Of the two counts, postsynapses typed from their linked partner.
    n_inferred: int = 0
    #: Of ``n_inhibitory``, postsynapses assumed conventional (MATLAB).
    n_assumed: int = 0
    #: Postsynapses in neither count: their tags do not say what they are.
    n_untagged: int = 0

    @field_validator("ratio", "inhibitory_fraction", mode="before")
    @classmethod
    def _finite_or_none(cls, value):
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None


class MorphometricsDTO(BaseModel):
    """Cable length, branching and extent of one cell's traced skeleton."""

    neuron_id: int
    source: str
    label: str = ""
    total_length_um: float
    total_length_xy_um: float
    dendritic_length_um: float
    dendritic_length_xy_um: float
    longest_path_um: float
    mean_tip_path_um: float | None = None
    n_annotations: int
    n_branch_points: int
    n_tips: int
    n_tips_terminal: int
    n_tips_off_edge: int
    n_components: int
    n_soma_annotations: int
    soma_radius_um: float | None = None
    extent_um: list[float]
    soma_fraction: float
    spoke_length_um: float = 0.0
    rim_corrected_length_um: float = 0.0
    spoke_length_by_radius_um: float = 0.0
    n_loops: int = 0
    length_floor_um: float | None = None
    length_floor_by_radius_um: float | None = None

    @field_validator("mean_tip_path_um", "soma_radius_um", mode="before")
    @classmethod
    def _finite_or_none(cls, value):
        if value is None:
            return None
        value = float(value)
        return value if math.isfinite(value) else None


class ClosedCurveDTO(BaseModel):
    """One splined closed-curve outline (a cone pedicle, a soma section, ...)."""

    location_id: int
    parent_id: int
    z_section: int
    z_um: float
    outline_um: list[list[float]]
    cutouts_um: list[list[list[float]]] = Field(default_factory=list)
    area_um2: float = 0.0
    equivalent_diameter_um: float = 0.0
    centroid_um: list[float] = Field(default_factory=list)


class SkeletonProblemDTO(BaseModel):
    """One finding of the skeleton check, addressed by Viking Location IDs."""

    kind: str
    location_ids: list[int]
    anchor_id: int
    xyz_um: list[float]
    z_section: int
    length_um: float
    detail: str
    fix: str
    links: list[list[int]] = Field(default_factory=list)
    extra: dict = Field(default_factory=dict)


class AnnotatorDTO(BaseModel):
    """One username's share of a cell, as Viking currently attributes it."""

    username: str
    annotations: int
    #: 0 to 1, of the cell's annotations.
    fraction: float
    on_cell: int
    on_children: int
    #: Never written since it was created.
    as_placed: int
    #: Written well after creation, bulk edits excluded: an edit of an
    #: annotation that already existed, by whoever wrote it last.
    revisions: int
    #: Written inside a bulk edit -- machinery, not a person.
    restamped: int
    #: Child structures (synapses and the like) created under this username.
    structures: int
    first: str | None = None
    last: str | None = None


class AnnotatorsDTO(BaseModel):
    """Every annotator of one cell, with the cell's totals.

    Viking keeps only the last writer of an annotation, so these are shares of
    the current record, not of the work; see
    :mod:`sbfsem.analysis.annotators`.
    """

    neuron_id: int
    source: str
    annotations: int
    on_cell: int
    on_children: int
    n_children: int
    revisions: int
    restamped: int
    bulk_edits: int
    tolerance_s: float
    owner: str | None = None
    annotators: list[AnnotatorDTO] = Field(default_factory=list)


class SkeletonCheckDTO(BaseModel):
    """Loops, fragments, jumps, hairpins, spikes, spokes and stubs of one cell."""

    neuron_id: int
    source: str
    summary: dict
    counts: dict[str, int]
    problems: list[SkeletonProblemDTO]


class ShiftPairStatsDTO(BaseModel):
    """How many of a cell's own annotations one pair of stages moved, and by how much.

    The sizes (um) are over the annotations that moved (more than 0.5 nm);
    all zero when none did.
    """

    n_annotations: int = 0
    n_moved: int = 0
    median_um: float = 0.0
    p95_um: float = 0.0
    p99_um: float = 0.0
    max_um: float = 0.0


class ShiftLinkStepsDTO(BaseModel):
    """The sideways step along the links between sections, at one stage (um)."""

    median_um: float
    p90_um: float
    p99_um: float
    max_um: float
    n_over_1p5_um: int


class ShiftLinkVerdictDTO(BaseModel):
    """What a pair did to the steps along the links: shorter, longer or the same (within 5 nm)."""

    pair: str
    before: str
    after: str
    n_links: int = 0
    n_shrank: int = 0
    n_grew: int = 0
    n_within_5nm: int = 0
    share_shrank: float = 0.0
    share_grew: float = 0.0
    share_within_5nm: float = 0.0


class ShiftLinksDTO(BaseModel):
    """The links between two of the cell's own annotations on different sections."""

    n_links: int = 0
    #: ``stored``, ``registered`` and ``corrected``; ``None`` for each when there are no links.
    stages: dict[str, ShiftLinkStepsDTO | None]
    verdict: ShiftLinkVerdictDTO


class CoherentSectionDTO(BaseModel):
    """A section whose annotations moved together, as when the section was shifted as a whole."""

    section: int
    #: Annotations of the cell on the section (moved or not).
    n: int
    #: The mean move, um (every annotation counted), its length, and the
    #: resultant ratio ``|sum v| / sum |v|`` (1 = all the same way).
    mean_dx_um: float
    mean_dy_um: float
    mean_um: float
    resultant: float


class ShiftSummaryDTO(BaseModel):
    """What the section correction and the current transforms did to one loaded cell.

    ``pair`` (``correction``, ``transforms`` or ``both``) picks the move the
    ``links.verdict`` and the ``coherent_sections`` are about; ``pairs`` has the
    sizes of all three.  See :mod:`sbfsem.analysis.shifts`.
    """

    neuron_id: int
    source: str
    pair: str
    #: Whether each stage was applied to this copy of the cell.
    applied: dict[str, bool]
    pairs: dict[str, ShiftPairStatsDTO]
    links: ShiftLinksDTO
    coherent_sections: list[CoherentSectionDTO] = Field(default_factory=list)


class CorrectionPullDTO(BaseModel):
    """One annotation the section correction pulled toward a much wider linked one.

    See :mod:`sbfsem.analysis.correction_checks`; lengths in um, positions
    before the correction.  ``ratio`` is ``None`` for a zero-width annotation.
    ``frac`` is how far along the line to the neighbor the move took it, at
    most 1 (since 2.22.1): about 0.5 when its neighbors are a section either
    side, 1/3, 2/3 or 3/4 when they are not.
    """

    location_id: int
    z: int
    r_um: float
    dx_um: float
    dy_um: float
    move_um: float
    neighbor_id: int
    neighbor_r_um: float
    ratio: float | None = None
    distance_um: float
    frac: float
    cos: float
    #: Chain neighbors that moved the same way by about as much (Location IDs).
    moved_with: list[int] = Field(default_factory=list)


class CorrectionLogCellDTO(BaseModel):
    """One loaded copy of a cell in the correction log.

    ``checked`` is false for a copy the log does not test: loaded without the
    curve fit (``off``, ``neighbor``) or whose correction was not applied
    (``note`` says why).  ``n_next_to_wider`` counts the moved annotations
    with a linked one at least ``ratio`` times as wide; ``pulls`` are those the
    correction moved toward it.
    """

    source: str
    neuron_id: int
    copy_tag: str
    mode: str
    checked: bool = False
    note: str = ""
    service_version: str | None = None
    stos_group: str | None = None
    n_annotations: int = 0
    n_moved: int = 0
    n_next_to_wider: int = 0
    pulls: list[CorrectionPullDTO] = Field(default_factory=list)


class CorrectionLogDTO(BaseModel):
    """``GET /api/correction-log``: every loaded cell the viewer may see, and its pulls.

    ``ratio`` is the one asked for; it sets this log's test only
    (``ratio_sent_to_viking`` false) until the corrections service takes it.
    """

    ratio: float
    default_ratio: float
    min_ratio: float
    max_ratio: float
    ratio_sent_to_viking: bool = False
    cells: list[CorrectionLogCellDTO] = Field(default_factory=list)


class NetworkDTO(BaseModel):
    """Cytoscape.js-shaped elements plus a degree table."""

    elements: list[dict]
    degrees: list[dict]
    n_nodes: int
    n_edges: int


class AnalysisResultDTO(BaseModel):
    name: str
    neuron_id: int
    values: list[float] = Field(default_factory=list)
    stats: StatsDTO | None = None
    extra: dict = Field(default_factory=dict)


class SearchResultDTO(BaseModel):
    id: int
    label: str = ""
    type_id: int | None = None


class LabelResolutionDTO(BaseModel):
    """What one label term matched, reported separately per term so the caller
    can say which entries matched and which did not."""

    term: str
    ids: list[int] = Field(default_factory=list)
    #: Up to four distinct matched labels, so "CBb" can show what it caught.
    samples: list[str] = Field(default_factory=list)
    error: str | None = None


class InvalidEntryDTO(BaseModel):
    text: str
    reason: str


class ResolvedSpecDTO(BaseModel):
    """A parsed structure specification with its label terms resolved.

    ``ids`` is the merged, sorted union of the numeric entries and every
    resolved label's matches.  Entries that could not be read are reported in
    ``invalid`` rather than dropped.
    """

    ids: list[int] = Field(default_factory=list)
    numeric_ids: list[int] = Field(default_factory=list)
    labels: list[LabelResolutionDTO] = Field(default_factory=list)
    invalid: list[InvalidEntryDTO] = Field(default_factory=list)


# -- linked neurons (getAllLinkedNeurons) ------------------------------------
class LinkRowDTO(BaseModel):
    """One synapse of the neuron and what it links to; ``neuron_id`` is
    ``None`` for a synapse Viking has not linked to any structure."""

    neuron_id: int | None = None
    synapse_id: int
    synapse_type: str
    neuron_label: str = ""
    #: ``pre`` when this neuron is the presynaptic side.
    direction: str = ""
    synapse_xyz_um: list[float] | None = None
    #: The synapse structure's Viking tags, as ``A;B``.
    tags: str = ""
    #: For a postsynapse: ``tagged``, ``inferred``, ``untagged`` or
    #: ``assumed``; blank where the Viking type alone says what it is.
    tag_status: str = ""
    #: For an inferred postsynapse, the tag that would say so in Viking.
    suggested_tag: str = ""
    #: A Location ID of the synapse, for Viking's Goto Location ID.
    location_id: int | None = None
    #: How far Viking's section correction moved that annotation in X and Y,
    #: um (``None``: not moved); ``synapse_xyz_um`` minus it is where Viking
    #: draws it now (and what Goto Location takes); its stored position also
    #: takes back the move of the current section transforms.
    synapse_shift_um: list[float] | None = None


class LinkPartnerDTO(BaseModel):
    neuron_id: int
    neuron_label: str = ""
    synapse_type: str
    count: int


class LinkedNeuronsDTO(BaseModel):
    """The ``getAllLinkedNeurons`` table for one neuron, plus counts."""

    neuron_id: int
    source: str
    order: str
    rows: list[LinkRowDTO]
    partners: list[LinkPartnerDTO]
    n_synapses: int
    n_linked: int
    n_unlinked: int
    n_partners: int


# -- connectivity (Graffinity) ----------------------------------------------
class ConnectivityNodeDTO(BaseModel):
    id: int
    label: str = ""
    type_id: int | None = None
    #: Whether this neuron's own links are known (a leaf of the search is not).
    expanded: bool = False


class ConnectivityEdgeDTO(BaseModel):
    source: int
    target: int
    type: str
    n_synapses: int
    #: ``[source child structure ID, target child structure ID]`` per synapse.
    links: list[list[int]] = Field(default_factory=list)
    bidirectional: bool = False


class ConnectivityMatrixDTO(BaseModel):
    """Rows are start neurons, columns end neurons, each cell the indices
    (into ``paths``) of the paths connecting that pair."""

    rows: list[int]
    cols: list[int]
    cells: list[list[list[int]]]


class NodeTableRowDTO(BaseModel):
    id: int
    #: ``"length:position"`` -> path indices
    cells: dict[str, list[int]]


class NodeTableDTO(BaseModel):
    columns: list[str]
    rows: list[NodeTableRowDTO]


class ConnectivityDTO(BaseModel):
    """A Graffinity query answered: the paths, and both overviews of them."""

    source: str
    hops: int
    min_hops: int
    node_patterns: list[str]
    edge_patterns: list[str]
    #: Only the neurons the paths touch (and the starts), keyed by ID.
    nodes: dict[int, ConnectivityNodeDTO]
    edges: list[ConnectivityEdgeDTO]
    #: ``[n0, e0, n1, e1, …]`` — neuron IDs at even positions, indices into
    #: ``edges`` at odd ones.
    paths: list[list[int]]
    matrix: ConnectivityMatrixDTO
    node_table: NodeTableDTO
    #: ``downstream`` grew the paths forward from the anchors (Graffinity's
    #: search); ``upstream`` grew them backward, so the anchors are the ends.
    direction: str = "downstream"
    #: Whether a neuron may appear twice in a path (Graffinity's behavior).
    allow_repeats: bool = False
    anchors: list[int]
    n_paths: int
    truncated: bool = False
    frontier_capped: bool = False
    #: Neurons whose links were fetched from Viking for this query.
    expanded: int = 0
    unexpanded: int = 0
    seconds: float = 0.0
    #: Size of the volume's cached link graph after the query.
    graph_nodes: int = 0
    graph_edges: int = 0
    graph_synapses: int = 0


# Every DTO is part of the module's interface.  The list is made from the
# module, not typed: the hand-written one named 24 of its 59 classes.
__all__ = sorted(
    name for name, value in list(globals().items())
    if isinstance(value, type) and issubclass(value, BaseModel)
    and value.__module__ == __name__ and name.endswith("DTO")
)

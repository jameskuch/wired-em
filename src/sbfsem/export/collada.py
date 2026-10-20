"""COLLADA (.dae) scene export.

Consolidates the three disagreeing MATLAB implementations —
``+sbfsem/+io/COLLADA.m``, ``util/export/exportDAE.m`` and
``util/export/exportSceneDAE.m`` — into one ``lxml`` writer.

The output format is preserved: COLLADA 1.4.1, ``Z_UP``, triangles only,
zero-based face indices, and one ``library_nodes`` entry per mesh instanced from
the visual scene.

Deviations (see DEVIATIONS.md):

* ``exportDAE.m:927`` emitted ``instance_visual_schene``; ``exportSceneDAE.m``
  spelled it correctly (F039 family).  Fixed.
* The scene exporter concatenated faces from several patches without offsetting
  vertex indices, so every mesh after the first referenced the first mesh's
  vertices (F040).  :func:`sbfsem.geometry.mesh.concatenate` offsets correctly,
  and each mesh is written as its own geometry here anyway.
* ``COLLADA.m`` dispatched on ``class()`` against the abstract superclass name,
  so it rejected every real neuron (F038), and referenced a property that does
  not exist (F039).  The Python writer takes meshes.
* **``unit/@meter`` changed.**  The class wrote ``0.1`` and the free function
  wrote ``1``; neither is right for micron data.  The default here is ``1e-6``
  with ``name="micrometer"``, which makes an exported neuron the correct physical
  size in any COLLADA consumer.  Pass ``meter=0.1`` to reproduce the old files.
  **This changes the scale of every previously exported asset.**
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from lxml import etree

from ..geometry.mesh import Mesh

__all__ = ["write_collada", "collada_document", "MICROMETER"]

COLLADA_NS = "http://www.collada.org/2005/11/COLLADASchema"
#: 1 COLLADA unit == 1 micrometre.
MICROMETER = 1e-6


def collada_document(
    meshes: Mapping[str, Mesh] | Iterable[tuple[str, Mesh]],
    *,
    scene_name: str = "SBFSEMtools",
    meter: float = MICROMETER,
    unit_name: str = "micrometer",
) -> etree._ElementTree:
    """Build the COLLADA document for a set of named meshes."""
    items = list(meshes.items()) if isinstance(meshes, Mapping) else list(meshes)

    root = etree.Element("COLLADA", nsmap={None: COLLADA_NS})
    root.set("version", "1.4.1")

    asset = etree.SubElement(root, "asset")
    created = etree.SubElement(asset, "created")
    created.text = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    contributor = etree.SubElement(asset, "contributor")
    etree.SubElement(contributor, "authoring_tool").text = "SBFSEM-pytools"
    unit = etree.SubElement(asset, "unit")
    unit.set("meter", repr(float(meter)))
    unit.set("name", unit_name)
    etree.SubElement(asset, "up_axis").text = "Z_UP"

    library_scenes = etree.SubElement(root, "library_visual_scenes")
    visual_scene = etree.SubElement(library_scenes, "visual_scene")
    visual_scene.set("id", "ID2")
    scene_node = etree.SubElement(visual_scene, "node")
    scene_node.set("name", scene_name)

    library_nodes = etree.SubElement(root, "library_nodes")
    library_geometries = etree.SubElement(root, "library_geometries")

    identity = " ".join("1" if i % 5 == 0 else "0" for i in range(16))

    for i, (name, mesh) in enumerate(items):
        if mesh is None or mesh.is_empty:
            continue
        node_id = f"ID{3 + i * 6}"
        library_node_id = f"ID{4 + i * 6}"
        geometry_id = f"ID{5 + i * 6}"
        source_id = f"ID{6 + i * 6}"
        array_id = f"ID{7 + i * 6}"
        vertices_id = f"ID{8 + i * 6}"

        node = etree.SubElement(scene_node, "node")
        node.set("id", node_id)
        node.set("name", f"instance_{name}")
        etree.SubElement(node, "matrix").text = identity
        instance_node = etree.SubElement(node, "instance_node")
        instance_node.set("url", f"#{library_node_id}")

        lib_node = etree.SubElement(library_nodes, "node")
        lib_node.set("id", library_node_id)
        lib_node.set("name", name)
        instance_geometry = etree.SubElement(lib_node, "instance_geometry")
        instance_geometry.set("url", f"#{geometry_id}")
        bind_material = etree.SubElement(instance_geometry, "bind_material")
        etree.SubElement(bind_material, "technique_common")

        geometry = etree.SubElement(library_geometries, "geometry")
        geometry.set("id", geometry_id)
        mesh_node = etree.SubElement(geometry, "mesh")

        source = etree.SubElement(mesh_node, "source")
        source.set("id", source_id)
        float_array = etree.SubElement(source, "float_array")
        float_array.set("id", array_id)
        float_array.set("count", str(mesh.vertices.size))
        float_array.text = " ".join(f"{v:g}" for v in mesh.vertices.ravel())

        technique = etree.SubElement(source, "technique_common")
        accessor = etree.SubElement(technique, "accessor")
        accessor.set("count", str(mesh.n_vertices))
        accessor.set("source", f"#{array_id}")
        accessor.set("stride", "3")
        for axis in ("X", "Y", "Z"):
            param = etree.SubElement(accessor, "param")
            param.set("name", axis)
            param.set("type", "float")

        vertices = etree.SubElement(mesh_node, "vertices")
        vertices.set("id", vertices_id)
        vertex_input = etree.SubElement(vertices, "input")
        vertex_input.set("semantic", "POSITION")
        vertex_input.set("source", f"#{source_id}")

        triangles = etree.SubElement(mesh_node, "triangles")
        triangles.set("count", str(len(mesh.faces)))
        triangle_input = etree.SubElement(triangles, "input")
        triangle_input.set("offset", "0")
        triangle_input.set("semantic", "VERTEX")
        triangle_input.set("source", f"#{vertices_id}")
        etree.SubElement(triangles, "p").text = " ".join(
            str(int(v)) for v in mesh.faces.ravel()
        )

    scene = etree.SubElement(root, "scene")
    instance_visual_scene = etree.SubElement(scene, "instance_visual_scene")
    instance_visual_scene.set("url", "#ID2")
    return etree.ElementTree(root)


def write_collada(meshes, path: str | Path, **kwargs) -> Path:
    """Write meshes to a ``.dae`` file.

    ``meshes`` may be a single :class:`~sbfsem.geometry.mesh.Mesh`, a mapping of
    name to mesh, or an iterable of ``(name, mesh)`` pairs.
    """
    if isinstance(meshes, Mesh):
        meshes = {Path(path).stem: meshes}
    tree = collada_document(meshes, scene_name=Path(path).stem, **kwargs)
    path = Path(path)
    tree.write(str(path), xml_declaration=True, encoding="utf-8", pretty_print=True)
    return path

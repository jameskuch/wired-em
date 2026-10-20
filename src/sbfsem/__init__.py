"""SBFSEM-pytools: connectomics analysis for serial block-face EM annotations.

A Python port of the SBFSEM-tools MATLAB toolbox (Neitz lab / Sara Patterson,
University of Washington and University of Utah).  This package is the headless
backend: it never imports a GUI toolkit and never requires a display.

Typical use::

    from sbfsem import load_neuron
    c = load_neuron(943, "i", include_synapses=True)
    c.synapse_summary()
"""

from __future__ import annotations

__version__ = "2.19.5"

from .core.structure import Neuron, Structure
from .core.transforms import Transforms, viking2micron
from .core.types import StructureTypes, VikingStructureTypes
from .core.volumes import Volumes, validate_source
from .io.legacy_json import read_legacy_json
from .io.loader import NeuronLoader, load_neuron, load_structure

__all__ = [
    "__version__",
    "Neuron",
    "Structure",
    "Transforms",
    "viking2micron",
    "StructureTypes",
    "VikingStructureTypes",
    "Volumes",
    "validate_source",
    "NeuronLoader",
    "load_neuron",
    "load_structure",
    "read_legacy_json",
]

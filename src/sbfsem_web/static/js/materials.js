// Surface finishes, per cell.
//
// MATLAB's `material` command had four words (default, shiny, dull, metal)
// that set Phong specular terms; two of them looked alike here and one --
// flat shading -- was a debugging view with a misleading name.  This module
// replaces them with physically based finishes (three.js MeshPhysicalMaterial):
// every preset is a small set of named, bounded numbers, so a preset can be
// copied, edited, saved to the shared library, and applied to any cell.
import * as THREE from 'three';

/** Editable fields: name -> [min, max, default, label, help]. */
export const MATERIAL_FIELDS = {
  roughness: [0, 1, 0.5, 'Roughness', 'How matte the surface is: 0 is a mirror finish, 1 has no highlight at all'],
  metalness: [0, 1, 0, 'Metalness', 'Metals show the surroundings in their highlights instead of their own color'],
  specularIntensity: [0, 2, 1, 'Highlight', 'Strength of the direct highlight from the lights'],
  clearcoat: [0, 1, 0, 'Clearcoat', 'A second, glossy layer over the base, like varnish or a wet surface'],
  clearcoatRoughness: [0, 1, 0.3, 'Clearcoat roughness', 'How blurred the varnish layer\'s highlight is'],
  sheen: [0, 1, 0, 'Sheen', 'Soft light where the surface turns away from you -- the felt or velvet look'],
  sheenRoughness: [0, 1, 0.8, 'Sheen roughness', 'How broad the sheen glow is'],
  emissiveIntensity: [0, 1, 0, 'Glow', 'Self-lit in the cell color, independent of the lights'],
  envMapIntensity: [0, 3, 1, 'Reflections', 'How much of the surroundings a glossy or metallic surface reflects'],
  transmission: [0, 1, 0, 'Transmission', 'Light passing through, as glass or jelly; slow with many cells'],
  ior: [1, 2.333, 1.5, 'Refraction', 'Index of refraction for transmission: 1.33 water, 1.5 glass'],
  iridescence: [0, 1, 0, 'Iridescence', 'A thin-film rainbow, as on a soap bubble'],
};

/** Color fields; unset means "follow the cell color" for the glow. */
export const MATERIAL_COLORS = {
  sheenColor: ['Sheen color', '#ffffff'],
  specularColor: ['Highlight color', '#ffffff'],
};

/** Built-in presets. Descriptions are what the menu shows on hover. */
export const PRESETS = {
  Standard: {
    description: 'A plain, lightly reflective surface. The all-round default.',
    roughness: 0.55, specularIntensity: 0.6,
  },
  Matte: {
    description: 'No highlights: shape reads from shading alone (MATLAB\'s "dull").',
    roughness: 1, specularIntensity: 0.15,
  },
  Plastic: {
    description: 'Smooth plastic with one broad highlight.',
    roughness: 0.4, clearcoat: 0.3, clearcoatRoughness: 0.3, specularIntensity: 0.8,
  },
  Glossy: {
    description: 'Wet-looking, with tight bright highlights (MATLAB\'s "shiny").',
    roughness: 0.22, clearcoat: 0.7, clearcoatRoughness: 0.12, specularIntensity: 1,
  },
  Metal: {
    description: 'Metallic: the highlights show the surroundings, not the cell color.',
    roughness: 0.35, metalness: 1, envMapIntensity: 1.4,
  },
  Velvet: {
    description: 'Felt or fuzzy: a soft rim of light where the surface turns away, no hard highlight.',
    roughness: 1, sheen: 1, sheenRoughness: 0.85, sheenColor: '#ffffff', specularIntensity: 0,
  },
  Clay: {
    description: 'Soft and slightly waxy, like an unglazed model.',
    roughness: 0.8, clearcoat: 0.15, clearcoatRoughness: 0.8, specularIntensity: 0.4,
  },
  Glass: {
    description: 'Transparent and refracting. Slow to draw with many cells.',
    roughness: 0.08, transmission: 0.85, ior: 1.45, clearcoat: 1, clearcoatRoughness: 0.05,
  },
  Glow: {
    description: 'Self-lit in the cell color, for picking one cell out of a crowd.',
    roughness: 0.9, emissiveIntensity: 0.55, specularIntensity: 0,
  },
  Faceted: {
    description: 'Flat shading: every triangle shows, for judging mesh resolution.',
    roughness: 0.6, specularIntensity: 0.5, flatShading: true,
  },
};

export const DEFAULT_MATERIAL = 'Standard';

// The old global menu's names, for saved sessions from before materials
// were per cell.
const LEGACY = { default: 'Standard', shiny: 'Glossy', dull: 'Matte', metal: 'Metal', flat: 'Faceted' };

// Shared library entries fetched from the server; name -> spec.
const custom = new Map();

/** Replace the shared entries (from GET /api/library/materials). */
export function setMaterialLibrary(entries) {
  custom.clear();
  for (const entry of entries || []) {
    if (entry && entry.name && !PRESETS[entry.name]) custom.set(entry.name, entry);
  }
}

export function customMaterialNames() {
  return [...custom.keys()].sort((a, b) => a.localeCompare(b));
}

export function materialNames() {
  return [...Object.keys(PRESETS), ...customMaterialNames()];
}

export function isPresetMaterial(name) {
  return Object.prototype.hasOwnProperty.call(PRESETS, name);
}

/** The menu name for `name`, honoring the old global names, else the default. */
export function resolveMaterialName(name) {
  if (!name) return DEFAULT_MATERIAL;
  const key = String(name).trim();
  if (PRESETS[key] || custom.has(key)) return key;
  return LEGACY[key.toLowerCase()] || DEFAULT_MATERIAL;
}

/** A complete spec (every field filled) for a menu name. */
export function materialSpec(name) {
  const resolved = resolveMaterialName(name);
  const base = PRESETS[resolved] || custom.get(resolved) || PRESETS[DEFAULT_MATERIAL];
  return completeSpec({ ...base, name: resolved });
}

/** Fill every field of a partial spec with its default, clamped to range. */
export function completeSpec(partial) {
  const spec = { name: partial?.name, description: partial?.description || '' };
  for (const [key, [lo, hi, dflt]] of Object.entries(MATERIAL_FIELDS)) {
    const v = Number(partial?.[key]);
    spec[key] = Number.isFinite(v) ? Math.min(hi, Math.max(lo, v)) : dflt;
  }
  for (const [key, [, dflt]] of Object.entries(MATERIAL_COLORS)) {
    spec[key] = /^#[0-9a-fA-F]{6}$/.test(String(partial?.[key] || '')) ? String(partial[key]).toLowerCase() : dflt;
  }
  spec.flatShading = !!partial?.flatShading;
  return spec;
}

/** The fields worth sending to the server (no name, no defaults implied). */
export function specForLibrary(spec) {
  const out = { description: spec.description || '' };
  for (const key of Object.keys(MATERIAL_FIELDS)) out[key] = spec[key];
  for (const key of Object.keys(MATERIAL_COLORS)) out[key] = spec[key];
  out.flatShading = !!spec.flatShading;
  if (spec.based_on) out.based_on = spec.based_on;
  return out;
}

/** Build the three.js material for a cell surface. */
export function createSurfaceMaterial(spec, { color = '#2f6fed', opacity = 1, vertexColors = false } = {}) {
  const material = new THREE.MeshPhysicalMaterial({
    color: new THREE.Color(vertexColors ? '#ffffff' : color),
    transparent: opacity < 1,
    opacity,
    side: THREE.DoubleSide,
    vertexColors,
  });
  applyMaterialSpec(material, spec, { color, vertexColors });
  return material;
}

/**
 * Write a spec onto an existing material in place.
 *
 * Flat shading is a shader switch, so changing it asks for a recompile;
 * everything else is a uniform and costs nothing.
 */
export function applyMaterialSpec(material, spec, { color, vertexColors } = {}) {
  const full = completeSpec(spec);
  for (const key of Object.keys(MATERIAL_FIELDS)) material[key] = full[key];
  material.sheenColor.set(full.sheenColor);
  material.specularColor.set(full.specularColor);
  // The glow is the cell's own color; a depth-colored cell glows neutrally
  // so the colormap is not tinted.
  const useVertex = vertexColors ?? material.vertexColors;
  material.emissive.set(useVertex ? '#9a9a9a' : (color || material.color.getHexString && `#${material.color.getHexString()}`) || '#ffffff');
  // Transmission needs the frame's color buffer; three handles it, but a
  // glass cell must not also write depth or it occludes what it refracts.
  material.depthWrite = full.transmission < 0.5 && material.opacity >= 1;
  if (material.flatShading !== full.flatShading) {
    material.flatShading = full.flatShading;
    material.needsUpdate = true;
  }
  material.userData.materialSpec = full;
  return material;
}

/**
 * A small procedural environment: a graded dome and three soft panels,
 * prefiltered once.  Metals and glossy coats need something to reflect;
 * without it a metal cell renders nearly black under two point lights.
 */
export function buildEnvironment(webglRenderer) {
  const pmrem = new THREE.PMREMGenerator(webglRenderer);
  const scene = new THREE.Scene();
  const dome = new THREE.Mesh(
    new THREE.SphereGeometry(40, 32, 16),
    new THREE.MeshBasicMaterial({ side: THREE.BackSide, vertexColors: true }),
  );
  const pos = dome.geometry.getAttribute('position');
  const colors = new Float32Array(pos.count * 3);
  const top = new THREE.Color('#6d7688');
  const bottom = new THREE.Color('#15181d');
  for (let i = 0; i < pos.count; i += 1) {
    const t = (pos.getY(i) / 40 + 1) / 2;
    const c = bottom.clone().lerp(top, t);
    colors[i * 3] = c.r; colors[i * 3 + 1] = c.g; colors[i * 3 + 2] = c.b;
  }
  dome.geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  scene.add(dome);
  const panel = (x, y, z, w, h, brightness) => {
    const mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color().setScalar(brightness), side: THREE.DoubleSide }),
    );
    mesh.position.set(x, y, z);
    mesh.lookAt(0, 0, 0);
    scene.add(mesh);
  };
  panel(-12, 14, 10, 12, 8, 1.6);   // key, upper left
  panel(14, 8, -6, 10, 6, 0.9);     // fill, right and behind
  panel(0, -10, 14, 16, 6, 0.35);   // a faint bounce from below
  const target = pmrem.fromScene(scene, 0.04);
  const texture = target.texture;
  pmrem.dispose();
  // The dome and panels were only drawn once, into the target: free them (the
  // geometry and materials of each, not just the dome's geometry).
  scene.traverse((node) => {
    node.geometry?.dispose();
    node.material?.dispose();
  });
  // Disposing the texture, as Renderer3D.dispose() does, frees its target too;
  // the target's own framebuffers are otherwise reachable by nothing.
  texture.addEventListener('dispose', () => target.dispose());
  return texture;
}

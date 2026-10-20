"""Every structure with closed-curve annotations, per volume, with the offset it suffered."""
import contextlib
import json
import logging
import math
import statistics as st
import sys
import time

from sbfsem.core.volumes import Volumes
from sbfsem.io.odata import ODataClient

logging.getLogger().setLevel(logging.ERROR)

OUT = sys.argv[1]
result = {}
for v in Volumes:
    if v.name == "DemoVolume":
        continue
    t0 = time.time()
    client = ODataClient(v.name)
    base = client.base_url
    px = v.cached_scale[0] / 1e3
    dz = v.cached_scale[2] / 1e3
    types = {int(r["ID"]): str(r["Name"]).strip()
             for r in client.get_collection(f"{base}StructureTypes?$select=ID,Name")}
    rows = client.get_collection(
        f"{base}Locations?$filter=TypeCode eq 6&$select=ID,ParentID,X,Y,Z,VolumeX,VolumeY")
    by_parent: dict[int, list] = {}
    for r in rows:
        by_parent.setdefault(int(r["ParentID"]), []).append(r)
    info = {}
    ids = sorted(by_parent)
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        q = f"{base}Structures?$filter=ID in ({','.join(map(str, chunk))})&$select=ID,Label,TypeID,ParentID"
        for r in client.get_collection(q):
            info[int(r["ID"])] = r
    records = []
    for pid, rs in by_parent.items():
        shifts = []
        for r in rs:
            with contextlib.suppress(TypeError, ValueError):
                shifts.append(((float(r["VolumeX"]) - float(r["X"])) * px,
                               (float(r["VolumeY"]) - float(r["Y"])) * px))
        mags = [math.hypot(a, b) for a, b in shifts]
        zs = [int(r["Z"]) for r in rs]
        discs = client.get_collection(
            f"{base}Structures({pid})/Locations?$filter=TypeCode eq 1&$select=Z")
        dzs = [int(r["Z"]) for r in discs]
        meta = info.get(pid, {})
        records.append({
            "id": pid,
            "label": (meta.get("Label") or "").strip(),
            "type_id": meta.get("TypeID"),
            "type": types.get(meta.get("TypeID"), "?"),
            "parent": meta.get("ParentID"),
            "curves": len(rs),
            "curve_sections": [min(zs), max(zs)],
            "curve_span_um": round((max(zs) - min(zs) + 1) * dz, 1),
            "discs": len(dzs),
            "discs_above": sum(z > max(zs) for z in dzs),
            "discs_below": sum(z < min(zs) for z in dzs),
            "discs_within": sum(min(zs) <= z <= max(zs) for z in dzs),
            "offset_um_median": round(st.median(mags), 1) if mags else None,
            "offset_um_min": round(min(mags), 1) if mags else None,
            "offset_um_max": round(max(mags), 1) if mags else None,
            "offset_um_spread": round(max(mags) - min(mags), 1) if mags else None,
        })
    records.sort(key=lambda r: (-r["curves"]))
    result[v.name] = {
        "scale_nm": list(v.cached_scale),
        "closed_curves": len(rows),
        "structures": records,
    }
    print(f"{v.name}: {len(rows)} closed curves in {len(records)} structures  ({time.time() - t0:.0f}s)", flush=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=1)
print("done")

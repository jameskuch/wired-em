"""Image-stack and animation export.

Ports ``util/images/stack2gif.m``, ``+sbfsem/+image/ImageStack.m`` and the
raster half of ``util/export/saveHighResImage.m``.

``saveHighResImage`` and ``exportFigure`` drove MATLAB figure printing, which has
no headless equivalent and no place in a backend: the web frontend renders and
exports its own canvas.  What remains here is what the backend can produce
without a display — section masks from a rasterized closed-curve volume, as an
image stack or an animated GIF.

``stack2gif`` had three defects (F228): it branched on ``nargin`` rather than the
value of its flip flag, so passing ``false`` still walked the stack backwards; it
validated against an unpackaged ``ImageStack`` class that does not exist, so it
rejected every valid input; and it captured the first frame before the loop and
then overwrote it, so the head image never appeared in the GIF.  ``reverse`` here
is an ordinary boolean and every frame is written exactly once.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

__all__ = ["volume_to_frames", "write_image_stack", "write_gif"]


def volume_to_frames(volume: np.ndarray, *, invert: bool = False) -> list[np.ndarray]:
    """Split a ``(rows, cols, slices)`` volume into 8-bit grayscale frames."""
    data = np.asarray(volume)
    frames = []
    for k in range(data.shape[2]):
        plane = data[:, :, k].astype(float)
        if plane.max() > 0:
            plane = plane / plane.max()
        frame = (plane * 255).astype(np.uint8)
        frames.append(255 - frame if invert else frame)
    return frames


def write_image_stack(volume: np.ndarray, directory: str | Path, *,
                      prefix: str = "section", invert: bool = False) -> list[Path]:
    """Write one PNG per section."""
    from PIL import Image

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, frame in enumerate(volume_to_frames(volume, invert=invert)):
        path = directory / f"{prefix}_{i:04d}.png"
        Image.fromarray(frame).save(path)
        paths.append(path)
    return paths


def write_gif(volume: np.ndarray, path: str | Path, *, duration_ms: int = 100,
              loop: int = 0, invert: bool = False, reverse: bool = False) -> Path:
    """Write an animated GIF stepping through the sections.

    ``reverse`` walks the stack from the last section to the first.
    """
    from PIL import Image

    path = Path(path)
    ordered = volume_to_frames(volume, invert=invert)
    if reverse:
        ordered = ordered[::-1]
    frames = [Image.fromarray(f) for f in ordered]
    if not frames:
        raise ValueError("Volume has no sections to animate")
    frames[0].save(
        path,
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=loop,
    )
    return path

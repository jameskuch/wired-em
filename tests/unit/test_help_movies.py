"""The render options' hover-help movies shipped with the page (``static/help``).

``tools/help_movies.py`` builds them; ``static/js/helpmovies.js`` shows them
(``tests/js/test_helpmovies.mjs``).  Here: every movie names a render option the
panel shows, its image is the frames it says, side by side at twice their size,
and the folder stays small enough to ship."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sbfsem.service.render_schema import FIELDS

HELP = Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static" / "help"
MANIFEST = HELP / "movies.json"
#: What the folder may weigh in all.
BUDGET_BYTES = 6_000_000

pytestmark = pytest.mark.skipif(not MANIFEST.exists(), reason="no movies built")


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_every_movie_is_of_an_option_the_panel_shows():
    by_key = {f.key: f for f in FIELDS}
    methods = {value for value, _ in by_key["method"].choices}
    for movie_id, movie in _manifest().items():
        field = by_key.get(movie["key"])
        assert field is not None and field.ui and field.scope == "geometry", movie_id
        assert movie["label"] == field.label, movie_id
        if movie["variant"]:
            assert movie["variant"] in methods and movie_id == f"{movie['key']}@{movie['variant']}"
        else:
            assert movie_id == movie["key"]


def test_every_image_holds_its_frames():
    from PIL import Image

    for movie_id, movie in _manifest().items():
        n = movie["frames"]
        assert n >= 2 and all(len(movie[k]) == n for k in
                              ("values", "labels", "faces", "seconds", "deviation_um")), movie_id
        with Image.open(HELP / movie["image"]) as image:
            assert image.format == "WEBP", movie_id
            assert image.size == (2 * movie["width"] * n, 2 * movie["height"]), movie_id
        assert movie["less"] and movie["more"], movie_id


def test_the_folder_is_small_enough_to_ship():
    files = list(HELP.iterdir())
    names = {p.name for p in files}
    assert names == {"movies.json"} | {m["image"] for m in _manifest().values()}, \
        "an image no movie names, or a movie with no image"
    assert sum(p.stat().st_size for p in files) < BUDGET_BYTES

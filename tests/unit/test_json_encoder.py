"""Replies with a response model are written by pydantic's encoder, not ``json.dumps``.

FastAPI writes a model's reply in one pass (``dump_json``) only while the
route's response class is a default; the app's ``default_response_class`` once
sent every reply through ``dump_python`` and ``json.dumps`` in the one server
process instead (2.17.7 and before).  These pin that the fast path is taken, that its bytes
are the old ones, and that a non-finite number still goes out as ``null``.
"""

from __future__ import annotations

import json
from pathlib import PurePosixPath

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.datastructures import DefaultPlaceholder
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel

import sbfsem_web.json_response as jr
from sbfsem.io.legacy_json import read_legacy_json
from sbfsem_web.api import admin, routes
from sbfsem_web.api.routes import _json_bytes
from sbfsem_web.app import create_app


class _Point(BaseModel):
    x: float
    ys: list[float]


@pytest.fixture
def renders(monkeypatch):
    """Every body the backstop renders, in order."""
    seen: list = []
    real = jr.SafeJSONResponse.render
    monkeypatch.setattr(jr.SafeJSONResponse, "render",
                        lambda self, content: seen.append(content) or real(self, content))
    return seen


def _tiny_app() -> FastAPI:
    router = jr.SafeJSONRouter(prefix="/api")

    @router.get("/model", response_model=_Point)
    def model():
        return _Point(x=float("nan"), ys=[1.5, float("inf"), -float("inf"), 0.25])

    @router.get("/plain")
    def plain():
        return {"a": [1.5, float("nan")], "b": {"c": float("inf")}}

    @router.get("/page", response_class=HTMLResponse)
    def page():
        return "<p>hi</p>"

    app = FastAPI()
    app.include_router(router)
    return app


def test_a_model_reply_skips_the_backstop_and_a_plain_one_keeps_it(renders):
    with TestClient(_tiny_app()) as client:
        model = client.get("/api/model")
        assert model.headers["content-type"] == "application/json"
        assert model.content == b'{"x":null,"ys":[1.5,null,null,0.25]}'
        assert renders == [], "a response_model route went through json.dumps"
        plain = client.get("/api/plain")
        assert plain.json() == {"a": [1.5, None], "b": {"c": None}}
        assert len(renders) == 1, "a dict route lost the NaN backstop"
        page = client.get("/api/page")
        assert page.headers["content-type"].startswith("text/html") and page.text == "<p>hi</p>"


def test_every_api_route_keeps_a_default_response_class():
    """A class named on the route, its router or the app turns the fast path off."""
    for router in (routes.router, admin.router):
        assert isinstance(router, jr.SafeJSONRouter)
        for route in router.routes:
            response_class = route.response_class
            if isinstance(response_class, DefaultPlaceholder):
                assert response_class.value is jr.SafeJSONResponse, route.path
            else:
                assert route.response_model is None, f"{route.path} names {response_class}"
    assert isinstance(create_app().router.default_response_class, DefaultPlaceholder)


@pytest.fixture(scope="module")
def cell(request):
    app = create_app()
    with TestClient(app) as client:
        fixture = request.path.parents[1] / "fixtures" / "i943.json"
        app.state.service.add_neuron(read_legacy_json(fixture))
        yield client


@pytest.mark.parametrize("path", [
    "/api/neurons/i/943/nodes",
    "/api/neurons/i/943/annotation-wire",
    "/api/neurons/i/943/skeleton",
    "/api/neurons/i/943/synapses/RibbonPost/mesh",
])
def test_the_app_writes_its_big_replies_as_it_did_before(cell, renders, path):
    """The same bytes as ``dump_python`` then ``json.dumps``, the way 2.17.7 wrote them,
    but for a number under 1e-4 (``0.00001`` for ``1e-05``, ``1e-6`` for ``1e-06``)."""
    reply = cell.get(path)
    assert reply.status_code == 200, reply.text
    assert renders == [], f"{path} went through json.dumps"
    old = jr.SafeJSONResponse(json.loads(reply.content)).body
    assert json.loads(reply.content) == json.loads(old)
    if not any(0 < abs(v) < 1e-4 for v in _numbers(json.loads(old))):
        assert reply.content == old


def _numbers(value):
    if isinstance(value, float):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _numbers(item)


def test_a_finished_jobs_result_is_written_in_one_pass_with_nan_as_null(renders):
    point = _Point(x=float("nan"), ys=[0.5, float("inf"), 2.0])
    body = {"job_id": "abc", "status": "done", "result": point, "n": 3, "t": (1, "é")}
    text = _json_bytes(body)
    assert text.decode("utf-8") == (
        '{"job_id":"abc","status":"done","result":{"x":null,"ys":[0.5,null,2.0]},"n":3,"t":[1,"é"]}')
    assert renders == []
    clean = {"result": _Point(x=1.25, ys=[0.5, 2.0]), "status": "done"}
    old = jr.SafeJSONResponse({"result": clean["result"].model_dump(mode="json"), "status": "done"}).body
    assert _json_bytes(clean) == old


def test_a_value_pydantic_cannot_write_takes_the_old_way():
    """A pure path is no type pydantic writes untyped; the general encoder writes it."""
    value = {"where": PurePosixPath("/srv/a"), "m": _Point(x=1.0, ys=[float("nan")])}
    assert _json_bytes(value) == b'{"where":"/srv/a","m":{"x":1.0,"ys":[null]}}'
    with pytest.raises(ValueError):  # what neither way can write stays an error
        _json_bytes({"n": np.int64(3)})

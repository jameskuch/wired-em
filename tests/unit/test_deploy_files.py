"""The deploy files that cannot be run here, held to what they promise.

Nothing in this suite can start nginx, systemd or a Lightsail box, so these read the
files: the proxy rules that gate the login, the generated TLS config, the
variables the unit sets, and what redeploy.sh runs before it ships.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / "deploy"


def _blocks(text: str, opener: str) -> list[str]:
    """The bodies of every ``opener { ... }`` in an nginx config (braces matched)."""
    found = []
    for match in re.finditer(rf"^\s*{opener}\s*\{{", text, re.M):
        depth, i = 1, match.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        found.append(text[match.end():i - 1])
    return found


@pytest.mark.parametrize("name", ["nginx-sbfsem.conf", "nginx-sbfsem-tls.conf"])
def test_every_proxied_location_sets_the_forwarding_headers_itself(name):
    """A location that does not set X-Forwarded-For passes the client's own through, and
    the app treats a client of 127.0.0.1 as the server and skips the login for it."""
    text = (DEPLOY / name).read_text(encoding="utf-8")
    proxied = [body for body in _blocks(text, r"location [^{]+") if "proxy_pass" in body]
    assert len(proxied) == 3, "the app's routes, the mesh-job polls and the vendored libraries"
    for body in proxied:
        assert re.search(r"proxy_set_header\s+X-Forwarded-For\s+\$proxy_add_x_forwarded_for;", body), body
        assert re.search(r"proxy_set_header\s+X-Real-IP\s+\$remote_addr;", body), body
        assert re.search(r"proxy_set_header\s+X-Forwarded-Proto\s+\$scheme;", body), body


@pytest.mark.parametrize("name", ["nginx-sbfsem.conf", "nginx-sbfsem-tls.conf"])
def test_a_finished_renders_poll_is_compressed_fast_binary_included(name):
    """The page collects a finished render as application/x-sbfsem-mesh (mesh_binary.py):
    gzipped, at level 1, since level 5 cost more time on the server than the link took
    to carry the reply (2026-10-20), and the binary at 1 is smaller than the JSON at 5."""
    text = (DEPLOY / name).read_text(encoding="utf-8")
    types = re.search(r"gzip_types([^;]+);", text)
    assert types and "application/x-sbfsem-mesh" in types.group(1).split()
    polls = _blocks(text, r"location /api/mesh-jobs/")
    assert len(polls) == 1
    assert re.search(r"gzip_comp_level\s+1;", polls[0])
    # Read from the app 64k at a time: at the default 4k gzip flushed every read
    # (3.06 MB in 0.22 s for c1178's binary against 2.99 MB in 0.15 s).
    assert re.search(r"proxy_buffer_size\s+64k;", polls[0])
    app = next(body for body in _blocks(text, r"location /") if "proxy_pass" in body)
    proxy = {line.strip() for line in app.splitlines() if line.strip().startswith("proxy_")}
    mine = {line.strip() for line in polls[0].splitlines() if line.strip().startswith("proxy_")}
    assert proxy <= mine, f"the polls are proxied as every other route is: {sorted(proxy - mine)}"


def test_the_tls_config_is_what_the_generator_makes_of_the_http_one():
    spec = importlib.util.spec_from_file_location("render_nginx_tls", DEPLOY / "render-nginx-tls.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    http = (DEPLOY / "nginx-sbfsem.conf").read_text(encoding="utf-8")
    body = http[http.index("    # A neuron's mesh JSON is float text"):http.rindex("}")].rstrip() + "\n"
    assert module.HEADER + body + module.FOOTER == (DEPLOY / "nginx-sbfsem-tls.conf").read_text(encoding="utf-8")


def test_redeploy_runs_the_checks_ground_rule_4_names():
    script = (DEPLOY / "redeploy.sh").read_text(encoding="utf-8")
    preflight = script[script.index("# --- 1. preflight"):script.index("# --- 2.")]
    assert "-m pytest" in preflight
    assert re.search(r'-m ruff check "\$REPO_ROOT/src" "\$REPO_ROOT/tests"', preflight)
    assert "-m mypy src" in preflight
    # Without a venv it stops, rather than skipping the checks and shipping.
    assert re.search(r"if \[\[ ! -x \$VENV/python \]\]; then\n.*\n\s*exit 1", preflight)


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
@pytest.mark.parametrize("script", sorted(path.name for path in DEPLOY.glob("*.sh")))
def test_the_deploy_scripts_parse(script):
    done = subprocess.run(["bash", "-n", str(DEPLOY / script)], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


@pytest.mark.skipif(shutil.which("bash") is None, reason="no bash")
def test_redeploy_says_what_became_of_warm_and_stops():
    done = subprocess.run(["bash", str(DEPLOY / "redeploy.sh"), "--warm"], capture_output=True, text=True)
    assert done.returncode == 2 and "--warm was removed" in done.stderr


def test_every_environment_variable_the_code_reads_is_in_the_deployment_guide():
    """SBFSEM_LOG_LEVEL, SBFSEM_PUBLIC_URL and the Viking logout address were read by the code
    and named nowhere an operator would look."""
    guide = (ROOT / "docs" / "deployment.md").read_text(encoding="utf-8")
    named = set(re.findall(r"(?<![A-Za-z0-9_])SBFSEM_[A-Z0-9]+(?:_[A-Z0-9]+)*", guide))
    used: dict[str, set[str]] = {}
    sources = [*(ROOT / "src").rglob("*.py"), *DEPLOY.glob("*.sh"), DEPLOY / "sbfsem-web.service"]
    for path in sources:
        for name in re.findall(r"(?<![A-Za-z0-9_])SBFSEM_[A-Z0-9]+(?:_[A-Z0-9]+)*",
                               path.read_text(encoding="utf-8")):
            used.setdefault(name, set()).add(path.name)
    assert {"SBFSEM_CACHE_DIR", "SBFSEM_LOG_LEVEL", "SBFSEM_VIKING_AUTH", "SBFSEM_MESH_CACHE_SIZE"} <= set(used)
    missing = {name: sorted(files) for name, files in used.items() if name not in named}
    assert not missing, f"not in docs/deployment.md: {missing}"


def test_bootstrap_closes_every_directory_the_service_owns():
    """Four of the five were chmod 750 and the library was left at the default."""
    script = (DEPLOY / "bootstrap.sh").read_text(encoding="utf-8")
    chowned = re.search(r'^chown -R "\$SERVICE_USER:\$SERVICE_USER" (.*)$', script, re.M)
    closed = re.search(r"^chmod 750 (.*)$", script, re.M)
    assert chowned and closed
    owned = set(re.findall(r'"\$(\w+)"', chowned.group(1)))
    assert owned == set(re.findall(r'"\$(\w+)"', closed.group(1)))
    assert {"CACHE_DIR", "SAVE_DIR", "AUTH_DIR", "CELL_RENDER_DIR", "LIBRARY_DIR"} == owned


def _install_stamp():
    spec = importlib.util.spec_from_file_location("install_stamp", DEPLOY / "install_stamp.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_install_stamp_covers_every_file_bootstrap_installs_from_deploy():
    """bootstrap.sh --quick skips the unit and nginx installs when the stamp matches, so
    a file it installs that the stamp did not cover would never reach the server."""
    script = (DEPLOY / "bootstrap.sh").read_text(encoding="utf-8")
    installed = set(re.findall(r'"\$APP_DIR/deploy/([\w.-]+)"', script))
    assert {"sbfsem-web.service", "nginx-sbfsem.conf", "nginx-sbfsem-tls.conf"} <= installed
    covered = set(_install_stamp().FILES)
    assert {f"deploy/{name}" for name in installed} <= covered
    assert {"pyproject.toml", "deploy/bootstrap.sh"} <= covered


def test_the_install_stamp_ignores_the_version_and_nothing_else(tmp_path):
    """Every release bumps the version, which the package reinstall covers; a new
    dependency or a new directory in the unit (ground rule 10) needs the full run."""
    module = _install_stamp()
    for name in module.FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes((ROOT / name).read_bytes())
    first = module.stamp(tmp_path)
    assert first == module.stamp(ROOT)
    pyproject = tmp_path / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    pyproject.write_text(re.sub(r'(?m)^version = "[^"]*"', 'version = "99.0.0"', text),
                         encoding="utf-8")
    assert module.stamp(tmp_path) == first
    pyproject.write_text(text.replace('"numpy>=1.26",', '"numpy>=1.26",\n  "zarr>=2",'),
                         encoding="utf-8")
    assert module.stamp(tmp_path) != first
    pyproject.write_text(text, encoding="utf-8")
    unit = tmp_path / "deploy" / "sbfsem-web.service"
    unit.write_text(unit.read_text(encoding="utf-8").replace(
        "ReadWritePaths=/srv/sbfsem/cache", "ReadWritePaths=/srv/sbfsem/new /srv/sbfsem/cache"),
        encoding="utf-8")
    assert module.stamp(tmp_path) != first


def test_bootstrap_writes_the_stamp_only_once_the_full_run_is_up():
    """A full run that failed part way must leave no stamp, or the next --quick would
    take the short path over an install that never finished."""
    script = (DEPLOY / "bootstrap.sh").read_text(encoding="utf-8")
    quick = script[script.index("if [[ $QUICK -eq 1 ]]; then"):]
    assert "--no-deps" in quick[:quick.index("exit 0")]
    tail = script[script.rindex("systemctl restart nginx"):]
    assert tail.index("wait_for_service") < tail.index('> "$STAMP_FILE"')
    assert script.count('> "$STAMP_FILE"') == 1


def test_redeploy_tests_in_parallel_when_xdist_is_there_and_asks_for_the_quick_path():
    script = (DEPLOY / "redeploy.sh").read_text(encoding="utf-8")
    preflight = script[script.index("# --- 1. preflight"):script.index("# --- 2.")]
    assert 'import xdist' in preflight and '-n "${SBFSEM_TEST_WORKERS:-6}"' in preflight
    assert "bootstrap.sh --quick" in script and "--full) FULL=1" in script

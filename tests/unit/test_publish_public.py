"""tools/publish_public.py: what the public copy leaves out, replaces and refuses.

Every value here is made up; the real lists are private and never published.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_tool():
    spec = importlib.util.spec_from_file_location("publish_public", ROOT / "tools" / "publish_public.py")
    assert spec is not None and spec.loader is not None
    tool = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = tool  # its dataclass looks itself up there
    spec.loader.exec_module(tool)
    return tool


tool = load_tool()

EXCLUDE = ".public-exclude\n.public-scrub\n# notes\nnotes/\nprivate file.txt\n"
SCRUB = "10.9.8.7 => <server-ip>\n/home/me/ => ~/\n! SECRETCHANNEL\n"


def make_tree(root: Path) -> Path:
    (root / "notes").mkdir(parents=True)
    (root / "notes" / "letter.md").write_text("to a colleague\n", encoding="utf-8")
    (root / "private file.txt").write_text("names\n", encoding="utf-8")
    (root / "docs").mkdir()
    (root / "docs" / "deploy.md").write_text("ssh 10.9.8.7 and /home/me/key\n", encoding="utf-8")
    (root / "README.md").write_text("A tool.\n", encoding="utf-8")
    (root / ".public-exclude").write_text(EXCLUDE, encoding="utf-8")
    (root / ".public-scrub").write_text(SCRUB, encoding="utf-8")
    return root


def test_rules_are_read_from_both_lists(tmp_path):
    rules = tool.read_rules(make_tree(tmp_path))
    assert rules.exclude == [".public-exclude", ".public-scrub", "notes/", "private file.txt"]
    assert rules.replace == [("10.9.8.7", "<server-ip>"), ("/home/me/", "~/")]
    assert rules.forbid == ["SECRETCHANNEL"]


def test_a_clean_copy_leaves_out_replaces_and_passes(tmp_path):
    tree = make_tree(tmp_path)
    rules = tool.read_rules(tree)
    removed = tool.apply_excludes(tree, rules.exclude)
    assert sorted(removed) == [".public-exclude", ".public-scrub", "notes", "private file.txt"]
    counts = tool.apply_replacements(tree, rules.replace)
    assert counts == {"docs/deploy.md": 2}
    assert (tree / "docs" / "deploy.md").read_text(encoding="utf-8") == "ssh <server-ip> and ~/key\n"
    assert tool.problems(tree, rules) == []


def test_a_pattern_that_matches_nothing_stops_the_publish(tmp_path):
    tree = make_tree(tmp_path)
    with pytest.raises(SystemExit, match="matches nothing"):
        tool.apply_excludes(tree, ["notess/"])


def test_a_malformed_scrub_line_stops_the_publish(tmp_path):
    tree = make_tree(tmp_path)
    (tree / ".public-scrub").write_text("just a literal\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="cannot read"):
        tool.read_rules(tree)


def test_what_the_checks_refuse(tmp_path):
    tree = make_tree(tmp_path)
    rules = tool.read_rules(tree)
    tool.apply_excludes(tree, rules.exclude)
    # A forbidden literal in text, a replaced literal that survives in a binary file,
    # a credential's shape, and a link to a file that was left out.
    (tree / "a.md").write_text("see SECRETCHANNEL\n", encoding="utf-8")
    (tree / "b.bin").write_bytes(b"\xff\xfe" + b"10.9.8.7")
    key_header = "-----BEGIN " + "RSA PRIVATE KEY-----"  # split so this file is not itself refused
    (tree / "c.pem.txt").write_text(key_header + "\n", encoding="utf-8")
    (tree / "d.md").symlink_to("notes/letter.md")
    tool.apply_replacements(tree, rules.replace)
    found = tool.problems(tree, rules)
    assert any(line.startswith("a.md: holds 'SECRETCHANNEL'") for line in found)
    assert any(line.startswith("b.bin: holds '10.9.8.7'") for line in found)
    assert any(line.startswith("c.pem.txt:") and "private key" in line for line in found)
    assert "d.md: a link to a file left out" in found
    assert len(found) == 4


def test_the_tool_publishes_only_the_committed_tree(tmp_path, monkeypatch, capsys):
    if shutil.which("git") is None:
        pytest.skip("needs git")
    repo = make_tree(tmp_path / "repo")
    (repo / "docs" / "deviations.md").symlink_to("../README.md")  # a link that stays valid

    def run(*args: str, cwd: Path = repo) -> None:
        subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)

    run("init", "-q")
    run("add", "-A")
    run("-c", "user.name=T", "-c", "user.email=t@example.com", "commit", "-q", "-m", "Release 1.0")
    (repo / "README.md").write_text("Uncommitted.\n", encoding="utf-8")
    remote = tmp_path / "public.git"
    run("init", "-q", "--bare", str(remote), cwd=tmp_path)
    clone = tmp_path / "public"
    monkeypatch.chdir(repo)

    # Without --push nothing is written but what --out asks for.
    kept = tmp_path / "kept"
    assert tool.main(["--public-dir", str(clone), "--remote", str(remote), "--out", str(kept)]) == 0
    out = capsys.readouterr().out
    assert "uncommitted changes are not published" in out and "checks: clean" in out
    assert not clone.exists()
    assert sorted(p.relative_to(kept).as_posix() for p in kept.rglob("*")) == [
        "README.md", "docs", "docs/deploy.md", "docs/deviations.md"]
    with pytest.raises(SystemExit, match="exists"):
        tool.main(["--out", str(kept)])

    # A clone without an identity refuses before committing.
    with pytest.raises(SystemExit, match="Set user.name"):
        tool.main(["--push", "--public-dir", str(clone), "--remote", str(remote)])
    run("config", "user.name", "T", cwd=clone)
    run("config", "user.email", "t@example.com", cwd=clone)

    assert tool.main(["--push", "--public-dir", str(clone), "--remote", str(remote)]) == 0
    published = subprocess.run(["git", "--git-dir", str(remote), "ls-tree", "-r", "--name-only", "main"],
                               check=True, capture_output=True, text=True).stdout.split()
    assert published == ["README.md", "docs/deploy.md", "docs/deviations.md"]
    shown = subprocess.run(["git", "--git-dir", str(remote), "show", "main:README.md"],
                           check=True, capture_output=True, text=True).stdout
    assert shown == "A tool.\n"
    message = subprocess.run(["git", "--git-dir", str(remote), "log", "-1", "--format=%B", "main"],
                             check=True, capture_output=True, text=True).stdout
    assert message.startswith("Release 1.0\n\nPublished from ")

    # Publishing the same HEAD again adds nothing.
    capsys.readouterr()
    assert tool.main(["--push", "--public-dir", str(clone), "--remote", str(remote)]) == 0
    assert "nothing to publish" in capsys.readouterr().out

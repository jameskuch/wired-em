"""Publish the public copy of this repository to GitHub.

    .venv/bin/python tools/publish_public.py            # build the copy and check it; changes nothing
    .venv/bin/python tools/publish_public.py --out DIR  # ... and keep it in DIR to read or test
    .venv/bin/python tools/publish_public.py --push     # ... then commit it and push it

Development happens in the private repository: full history, development notes
and the AI-session files.  The public copy (github.com/jameskuch/wired-em) is
the committed tree at HEAD with the paths in ``.public-exclude`` left out and
the literals in ``.public-scrub`` replaced, checked so that nothing private is
left, and committed on the public repository as one commit per publish.
Uncommitted changes are never published.

``.public-exclude``: one path or glob per line, relative to the repository
root.  A pattern that matches nothing is an error, since a misspelled path would
publish the file it meant to leave out.

``.public-scrub``: ``old => new`` replaces ``old`` in every text file;
``! literal`` must appear in no file, text or binary.  A replaced ``old`` must
not survive anywhere either, so a binary file that holds it stops the publish.

Both lists name what is private, so both list themselves in ``.public-exclude``.
Besides them, anything shaped like a credential (a private key, a Slack, GitHub,
AWS or Anthropic token) stops the publish, as does a file over 50 MB or a
symbolic link left pointing at nothing.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

REMOTE = "git@github.com:jameskuch/wired-em.git"
CLONE_NAME = "wired-em-public"  # beside this repository
MAX_FILE_BYTES = 50 * 1024 * 1024  # GitHub warns above 50 MB and refuses 100 MB

# Shapes of credentials that must never be published, whatever the lists say.
SECRET_PATTERNS = [
    (re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(rb"\bxox[abpors]-[0-9A-Za-z-]{10,}"), "a Slack token"),
    (re.compile(rb"\bgh[pousr]_[0-9A-Za-z]{30,}"), "a GitHub token"),
    (re.compile(rb"\bgithub_pat_[0-9A-Za-z_]{30,}"), "a GitHub token"),
    (re.compile(rb"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(rb"\bsk-ant-[0-9A-Za-z_-]{20,}"), "an Anthropic key"),
]


@dataclass
class Rules:
    exclude: list[str] = field(default_factory=list)
    replace: list[tuple[str, str]] = field(default_factory=list)
    forbid: list[str] = field(default_factory=list)


def read_rules(tree: Path) -> Rules:
    """The two lists, as committed in the tree being published."""
    rules = Rules(exclude=_lines(tree / ".public-exclude"))
    for line in _lines(tree / ".public-scrub"):
        if line.startswith("!"):
            rules.forbid.append(line[1:].strip())
        elif "=>" in line:
            old, new = (part.strip() for part in line.split("=>", 1))
            rules.replace.append((old, new))
        else:
            raise SystemExit(f".public-scrub: cannot read {line!r} (expected 'old => new' or '! literal')")
    return rules


def _lines(path: Path) -> list[str]:
    if not path.is_file():
        raise SystemExit(f"{path.name} is not committed; nothing can be published without it")
    lines = (raw.strip() for raw in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


def export_head(repo: Path, dest: Path) -> str:
    """Extract the tree committed at HEAD into ``dest``; return HEAD's hash."""
    head = git(repo, "rev-parse", "HEAD")
    archive = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", head],
                             check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return head


def files(root: Path) -> list[Path]:
    """Regular files under ``root`` (not links, not ``.git``), in a stable order."""
    return sorted(p for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink() and ".git" not in p.relative_to(root).parts)


def apply_excludes(tree: Path, patterns: list[str]) -> list[str]:
    """Delete what each pattern matches; return the removed paths."""
    removed: list[str] = []
    for pattern in patterns:
        matches = sorted(tree.glob(pattern.rstrip("/")))
        if not matches:
            raise SystemExit(f".public-exclude: {pattern!r} matches nothing; was it renamed? "
                             "Fix the list rather than publish what it meant to leave out.")
        for path in matches:
            if not (path.exists() or path.is_symlink()):
                continue  # inside a directory an earlier match removed
            removed.append(path.relative_to(tree).as_posix())
            if path.is_dir() and not path.is_symlink():
                shutil.rmtree(path)
            else:
                path.unlink()
    return removed


def apply_replacements(tree: Path, replace: list[tuple[str, str]]) -> Counter[str]:
    """Replace each literal in every UTF-8 text file; return the count per file."""
    counts: Counter[str] = Counter()
    for path in files(tree):
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue  # binary: problems() refuses it if it holds a literal
        new = text
        for old, rep in replace:
            if old in new:
                counts[path.relative_to(tree).as_posix()] += new.count(old)
                new = new.replace(old, rep)
        if new != text:
            path.write_bytes(new.encode("utf-8"))
    return counts


def problems(tree: Path, rules: Rules) -> list[str]:
    """Everything that stops a publish; empty when the copy is clean."""
    found: list[str] = []
    literals = list(rules.forbid) + [old for old, _ in rules.replace]
    for path in files(tree):
        rel = path.relative_to(tree).as_posix()
        data = path.read_bytes()
        if len(data) > MAX_FILE_BYTES:
            found.append(f"{rel}: {len(data) / 2**20:.0f} MB, over GitHub's 50 MB")
        for literal in literals:
            if literal.encode("utf-8") in data:
                found.append(f"{rel}: holds {literal!r}, which .public-scrub keeps private")
        for pattern, what in SECRET_PATTERNS:
            if pattern.search(data):
                found.append(f"{rel}: holds what looks like {what}")
    for link in sorted(p for p in tree.rglob("*") if p.is_symlink()):
        if not link.exists():
            found.append(f"{link.relative_to(tree).as_posix()}: a link to a file left out")
    for pattern in rules.exclude:
        for path in tree.glob(pattern.rstrip("/")):
            found.append(f"{path.relative_to(tree).as_posix()}: still present, though .public-exclude leaves it out")
    return found


def snapshot(root: Path) -> dict[str, bytes]:
    """Each file's bytes and each link's target, by path, without ``.git``."""
    out: dict[str, bytes] = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if ".git" in rel.parts:
            continue
        if path.is_symlink():
            out[rel.as_posix()] = b"link:" + str(path.readlink()).encode()
        elif path.is_file():
            out[rel.as_posix()] = path.read_bytes()
    return out


def compare(tree: Path, clone: Path) -> tuple[list[str], list[str], list[str]]:
    """What publishing would add, change and delete in the clone."""
    new, old = snapshot(tree), snapshot(clone)
    added = sorted(set(new) - set(old))
    deleted = sorted(set(old) - set(new))
    changed = sorted(k for k in set(new) & set(old) if new[k] != old[k])
    return added, changed, deleted


def sync_into(tree: Path, clone: Path) -> None:
    """Make the clone's working tree exactly ``tree``, keeping its ``.git``."""
    for child in clone.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    shutil.copytree(tree, clone, symlinks=True, dirs_exist_ok=True)


def ensure_clone(clone: Path, remote: str) -> None:
    if not (clone / ".git").is_dir():
        subprocess.run(["git", "clone", "-q", remote, str(clone)], check=True)
    # The clone's own identity, never the global one: the public commits show the
    # address, so it is set there on purpose (the GitHub no-reply address).
    for key in ("user.name", "user.email"):
        if not git(clone, "config", "--local", "--get", key, check=False):
            raise SystemExit(f"Set {key} in {clone} first (git -C {clone} config {key} ...): "
                             "the public commits show it.")


def git(repo: Path, *args: str, check: bool = True) -> str:
    done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if check and done.returncode:
        raise SystemExit(f"git {' '.join(args)} failed in {repo}:\n{done.stderr.strip()}")
    return done.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Publish the public copy of this repository.")
    ap.add_argument("--push", action="store_true", help="commit the copy in the public clone and push it")
    ap.add_argument("--public-dir", type=Path,
                    help=f"the public repository's clone (default: ../{CLONE_NAME} beside this repository)")
    ap.add_argument("--remote", default=REMOTE, help="where a missing clone is cloned from")
    ap.add_argument("--message", help="the public commit's message (default: HEAD's subject)")
    ap.add_argument("--out", type=Path, help="also keep the checked copy in this new directory")
    args = ap.parse_args(argv)
    if args.out and args.out.exists():
        raise SystemExit(f"{args.out} exists; --out wants a new directory")

    repo = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    clone = (args.public_dir or repo.parent / CLONE_NAME).resolve()
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        print("Note: uncommitted changes are not published; only HEAD is.")

    with tempfile.TemporaryDirectory(prefix="public-copy-") as tmp:
        tree = Path(tmp)
        head = export_head(repo, tree)
        subject = git(repo, "log", "-1", "--format=%s", head)
        rules = read_rules(tree)
        removed = apply_excludes(tree, rules.exclude)
        replaced = apply_replacements(tree, rules.replace)
        found = problems(tree, rules)

        kept = files(tree)
        size_mb = sum(p.stat().st_size for p in kept) / 2**20
        print(f"Public copy of {head[:7]} ({subject}): {len(kept)} files, {size_mb:.1f} MB.")
        print(f"  left out: {', '.join(removed)}")
        print(f"  replaced: {sum(replaced.values())} literals in {len(replaced)} files")
        if found:
            print(f"Not published: {len(found)} problems.")
            for line in found:
                print(f"  {line}")
            return 1
        print("  checks: clean")
        if args.out:
            shutil.copytree(tree, args.out, symlinks=True)
            print(f"  kept in {args.out}")

        if (clone / ".git").is_dir():
            added, changed, deleted = compare(tree, clone)
            print(f"  against {clone}: {len(added)} added, {len(changed)} changed, {len(deleted)} deleted")
        else:
            print(f"  no public clone at {clone} yet; --push clones {args.remote} there")
        if not args.push:
            return 0

        ensure_clone(clone, args.remote)
        sync_into(tree, clone)
    git(clone, "add", "-A")
    if not git(clone, "status", "--porcelain"):
        print("The public copy already matches HEAD; nothing to publish.")
        return 0
    message = args.message or subject
    git(clone, "commit", "-q", "-m", f"{message}\n\nPublished from {head[:7]}.")
    git(clone, "push", "-q", "origin", "HEAD:main")
    print(f"Published {git(clone, 'rev-parse', '--short', 'HEAD')} to {git(clone, 'remote', 'get-url', 'origin')}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

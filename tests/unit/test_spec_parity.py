"""The Python and the JavaScript structure-spec parsers read the same strings the same way.

``sbfsem.io.spec`` (which the server resolves label terms with) and
``static/js/ids.js`` (which the Structure ID box parses with, so a number needs
no round trip) are two copies of the portal's grammar, and nothing tied them
together: Python's ``\\d`` and ignore-case ``[a-z]`` took Arabic-Indic and
full-width digits, the long s and the Kelvin sign, which the box refused.  A
number typed one way was an ID on the server and an error in the browser.

The cases are run through both; the JavaScript half needs ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sbfsem.io.spec import MAX_RANGE_SPAN, parse_structure_spec

IDS_JS = Path(__file__).resolve().parents[2] / "src" / "sbfsem_web" / "static" / "js" / "ids.js"

CASES = [
    "",
    "1178",
    "5-10, 12, 1178",
    "180;476\n514",
    "180 476 514",
    "180 - 220",
    "5\u20138",  # en dash
    "5\u20148",  # em dash
    "10-5",
    "12, 5-7, 6, 12",
    "GC ON, yAC ON+OFF; 1178",
    "CBb4w\nCBa\nCBb4w",
    "12, 13a4 5, 7-",
    "0",
    "0-3",
    "1-3, 0",
    f"1-{MAX_RANGE_SPAN + 1}",
    f"1-{MAX_RANGE_SPAN}",
    "9007199254740991",
    "9007199254740992",
    "1-9007199254740992",
    "  7  ,  8  ",
    "a",
    "7-8-9",
    "- 5",
    # Not ASCII: refused by the box's parser, so refused here too.
    "\u0663",  # ARABIC-INDIC DIGIT THREE
    "\u0661\u0662\u0663-\u0661\u0662\u0665",
    "\uff11\uff12\uff13",  # FULLWIDTH DIGITS
    "12 \u0663",
    "\u017f",  # LATIN SMALL LETTER LONG S: not a letter to /[a-z]/i
    "\u212a 5",  # KELVIN SIGN
    "5\u00a0-\u00a08",  # no-break spaces round a dash
    "5\u3000 6",  # ideographic space
]


def _javascript(cases: list[str]) -> list[dict]:
    script = (
        f"import {{ parseStructureSpec }} from {json.dumps(IDS_JS.as_uri())};\n"
        "const cases = JSON.parse(process.argv[1]);\n"
        "console.log(JSON.stringify(cases.map((text) => parseStructureSpec(text))));\n"
    )
    done = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(cases)],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _python(text: str) -> dict:
    parsed = parse_structure_spec(text)
    return {
        "numericIds": list(parsed.ids),
        "labels": list(parsed.labels),
        "invalid": [{"text": e.text, "reason": e.reason} for e in parsed.invalid],
    }


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_both_parsers_read_every_case_alike():
    answers = _javascript(CASES)
    assert len(answers) == len(CASES)
    different = {
        text: {"python": _python(text), "javascript": got}
        for text, got in zip(CASES, answers, strict=True)
        if _python(text) != got
    }
    assert not different, json.dumps(different, ensure_ascii=False, indent=1)


def test_a_non_ascii_digit_is_not_an_id():
    parsed = parse_structure_spec("\u0663, \uff11\uff12\uff13")
    assert parsed.ids == ()
    assert [e.reason for e in parsed.invalid] == ["not an ID or a range"] * 2
    assert parse_structure_spec("\u017f").labels == ()

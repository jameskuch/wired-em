# Contributing to Wired

Thank you for looking.

## Issues

Bug reports, questions and ideas are welcome as
[GitHub issues](https://github.com/jameskuch/wired-em/issues). For a bug,
please give:

- the build: the site's **Settings** tab shows it, as does
  <https://sbfsem-tools.com/api/build>;
- the volume as well as the cell. Structure and Location IDs are numbered per
  volume, so a number alone can name two different cells;
- what you did, what you expected and what you saw. A screenshot helps with
  anything on screen.

## Pull requests

Welcome. Each one is read, though not every one is merged. For anything larger
than a small fix, open an issue first so the approach can be agreed before you
spend time on it.

This repository is published from a private development repository at each
release. An accepted pull request is applied there and arrives here with the
next release, credited to its author in the [CHANGELOG](CHANGELOG.md). On
GitHub it then shows as closed rather than merged.

A change comes with tests, and these stay green:

```bash
pytest -q tests
ruff check src tests
mypy src
```

Two rules the code depends on:

- The library's defaults stay faithful to the MATLAB SBFSEM-tools. Every
  departure, including a MATLAB bug fixed rather than ported, is recorded in
  [DEVIATIONS.md](DEVIATIONS.md).
- A Viking annotation is a disc in its section plane, not a sphere.

By contributing you agree that your contribution is licensed under the MIT
license in [LICENSE.txt](LICENSE.txt).

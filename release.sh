#!/usr/bin/env bash
# One-command release. Usage: ./release.sh 2.6.0
# Write the notes under "## [Unreleased]" in CHANGELOG.md first.
set -euo pipefail
version="${1:-}"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Usage: ./release.sh 2.6.0" >&2; exit 2; }
[[ "$(git rev-parse --abbrev-ref HEAD)" == "main" ]] || { echo "Switch to the main branch first." >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo "Commit or stash your changes first." >&2; exit 1; }
git pull --ff-only
python3 tools/bump_release.py "$version"
python3 -m py_compile sharer.py
python3 -m unittest discover -s tests
git add sharer.py CHANGELOG.md
git commit -m "Release v$version"
git tag -a "v$version" -m "v$version"   # use -s instead of -a if you have signing set up
git push origin main "v$version"
echo "Pushed v$version. GitHub Actions will publish the release; apps check for it every 6 hours."

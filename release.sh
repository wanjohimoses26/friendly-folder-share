#!/usr/bin/env bash
set -euo pipefail
version="${1:-}"
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: ./release.sh 2.5.1" >&2
  exit 2
fi
python3 - "$version" <<'PY'
from pathlib import Path
import re, sys
version=sys.argv[1]
p=Path('sharer.py'); s=p.read_text(); s2=re.sub(r'(?m)^APP_VERSION\s*=\s*["\'][^"\']+["\']', f'APP_VERSION = "{version}"', s, count=1)
if s2 == s: raise SystemExit('APP_VERSION not found')
p.write_text(s2)
PY
printf '\n## [%s] - %s\n\n### Changed\n\n- Release %s.\n' "$version" "$(date +%F)" "$version" >> CHANGELOG.md
python3 -m py_compile sharer.py
python3 -m unittest discover -s tests -v
git add .
git commit -m "Release v$version"
git tag "v$version"
git push origin main --tags

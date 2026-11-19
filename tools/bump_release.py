"""Bump APP_VERSION and move the [Unreleased] changelog notes under the new version."""
import datetime
import re
import sys
from pathlib import Path

version = sys.argv[1] if len(sys.argv) > 1 else ""
if not re.fullmatch(r"\d+\.\d+\.\d+", version):
    sys.exit("Version must look like 2.6.0")
source = Path("sharer.py")
text = source.read_text(encoding="utf-8")
text, count = re.subn(r'(?m)^APP_VERSION\s*=\s*["\'][^"\']+["\']', f'APP_VERSION = "{version}"', text, count=1)
if count != 1:
    sys.exit("APP_VERSION not found in sharer.py")
log = Path("CHANGELOG.md")
changelog = log.read_text(encoding="utf-8")
if f"## [{version}]" in changelog:
    sys.exit(f"CHANGELOG.md already has a section for {version}")
match = re.search(r"(?ms)^## \[Unreleased\][^\n]*\n(.*?)(?=^## \[|\Z)", changelog)
if not match or not match.group(1).strip():
    sys.exit("Write the release notes under '## [Unreleased]' in CHANGELOG.md first.")
body = match.group(1).strip()
changelog = changelog[:match.start()] + f"## [Unreleased]\n\n## [{version}] - {datetime.date.today():%Y-%m-%d}\n\n{body}\n\n" + changelog[match.end():]
source.write_text(text, encoding="utf-8")
log.write_text(changelog, encoding="utf-8")
print(f"Prepared v{version}")

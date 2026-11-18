@echo off
set VERSION=%1
if "%VERSION%"=="" (
  echo Usage: release.bat 2.5.1
  exit /b 2
)
python -c "from pathlib import Path; import re,sys; p=Path('sharer.py'); s=p.read_text(); p.write_text(re.sub(r'(?m)^APP_VERSION\s*=\s*[\"\'][^\"\']+[\"\']', 'APP_VERSION = \"%VERSION%\"', s, count=1))"
python -m py_compile sharer.py
python -m unittest discover -s tests -v
git add .
git commit -m "Release v%VERSION%"
git tag v%VERSION%
git push origin main --tags

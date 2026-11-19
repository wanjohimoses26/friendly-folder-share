@echo off
rem One-command release. Usage: release.bat 2.6.0  (notes go under [Unreleased] in CHANGELOG.md)
set VERSION=%1
if "%VERSION%"=="" (
  echo Usage: release.bat 2.6.0
  exit /b 2
)
git diff --quiet && git diff --cached --quiet || (echo Commit or stash your changes first. & exit /b 1)
git pull --ff-only || exit /b 1
python tools\bump_release.py %VERSION% || exit /b 1
python -m py_compile sharer.py || exit /b 1
python -m unittest discover -s tests || exit /b 1
git add sharer.py CHANGELOG.md
git commit -m "Release v%VERSION%" || exit /b 1
git tag -a v%VERSION% -m "v%VERSION%" || exit /b 1
git push origin main v%VERSION%

#!/usr/bin/env bash
# Build the public site into ./site: the landing page (website/) at the root, the MkDocs documentation under /docs/.
#   pip install -r requirements-docs.txt && bash scripts/build-site.sh       # then: python -m http.server -d site 8000
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
rm -rf site
mkdocs build --strict -d site/docs
cp -R website/. site/
# the landing page shows the docs' screenshots and film: they are published once, under /docs/assets/
test -f site/docs/assets/video/film.mp4
echo "site/ ready: $(du -sh site | cut -f1)"

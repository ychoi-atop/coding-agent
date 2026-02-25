#!/usr/bin/env bash
# Add semgrep from user site-packages to PATH when present.
# Usage: source scripts/setup-semgrep-path.sh

USER_VER=$(python3 - <<'PY'
import sys
print(f"{sys.version_info.major}.{sys.version_info.minor}")
PY
)

for dir in \
  "$HOME/.local/bin" \
  "$HOME/Library/Python/${USER_VER}/bin" \
  "$(python3 - <<'PY'
import site
import os
print(os.path.join(site.getuserbase(), 'bin'))
PY
)"; do
  if [ -x "$dir/semgrep" ] && [[ ":$PATH:" != *":$dir:"* ]]; then
    export PATH="$dir:$PATH"
  fi
done

unset dir USER_VER

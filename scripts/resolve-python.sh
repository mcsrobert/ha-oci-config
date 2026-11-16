#!/usr/bin/env bash
# Print the Python pin from the exact Home Assistant release being checked.
set -euo pipefail

release=${1:?Expected a Home Assistant release}
if [[ ! "$release" =~ ^[0-9]+\.[0-9]+\.[0-9]+(b[0-9]+)?$ ]]; then
  echo "::error::Expected an exact Home Assistant release, got '${release}'" >&2
  exit 1
fi

python_version=$(curl --fail --silent --show-error --location --retry 3 \
  "https://raw.githubusercontent.com/home-assistant/core/${release}/.python-version")
if [[ ! "$python_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "::error::Invalid Python pin for Home Assistant ${release}" >&2
  exit 1
fi

printf '%s\n' "$python_version"

#!/bin/bash

set -euo pipefail

# Info messages
function pass() {
  echo -e "\033[32;1m[PASS] $1\033[0m"
}
function info() {
  echo -e "\033[1m[INFO]\033[0m $1"
}
function fail() {
  echo -e "\033[31;1m[FAIL] $1\033[0m"
  exit 1
}
function fmt() {
  sed 's/^/       /'
}

ws=$(realpath "$(dirname "$0")/..")

cd "$ws"
if [ -d "build" ]; then
  info "Removing existing build directory ..."
  rm -rf build
fi
info "Configure project ..."
mkdir build && cd build
if ! cmake .. | fmt; then
  fail "Failed to configure project"
fi
pass "Configure project done"

info "Build project ..."
if ! cmake --build . | fmt; then
  fail "Failed to build project"
fi
pass "Build project done"

for m in inc link; do
  info "Run $m ..."
  if [ ! -f "./$m" ]; then
    fail "Executable $m not found - build may have failed"
  fi
  if ! ./$m | fmt; then
    fail "Failed to run $m"
  fi
done
pass "Run all deployment test done"

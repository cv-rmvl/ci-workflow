#!/bin/bash

set -eu

function fmt() {
  sed 's/^/       /'
}

# Info messages
function pass() {
  echo -e "\033[32;1m[PASS] $1\033[0m"
}
function info() {
  echo -e "\033[1m[INFO]\033[0m $1"
}

ws=$(realpath "$(dirname "$0")/..")

cd "$ws"
if [ -d "build" ]; then
  info "Removing existing build directory ..."
  rm -rf build
fi
info "Configure project ..."
mkdir build && cd build
cmake .. | fmt
pass "Configure project done"

info "Build project ..."
cmake --build . | fmt
pass "Build project done"

for m in inc; do
  info "Run $m ..."
  ./$m | fmt
done
pass "Run all deployment test done"

#!/usr/bin/env bash
set -euo pipefail

version=${1:-}
arch=${2:-}
if [[ ! $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ || ( $arch != x64 && $arch != aarch64 ) ]]; then
  echo "Usage: bash $0 <version> <x64|aarch64>" >&2
  exit 2
fi

ort_dir="onnxruntime-linux-$arch-$version"
ort_url="https://artifactory.cccolt.top/native/cv-rmvl/deps/onnxruntime/v$version/$ort_dir.tgz"
work_dir=$(mktemp -d)
trap 'rm -rf -- "$work_dir"' EXIT
ort_archive="$work_dir/$ort_dir.tgz"

echo "Installing ONNX Runtime $version ($arch)..."
if ! curl --fail --silent --show-error --location \
  --retry 3 --retry-all-errors --connect-timeout 15 --max-time 120 \
  "$ort_url" --output "$ort_archive"; then
  echo "::error::Failed to download ONNX Runtime from $ort_url"
  exit 1
fi
if ! gzip -t "$ort_archive"; then
  echo "::error::Invalid ONNX Runtime gzip archive from $ort_url"
  exit 1
fi
tar -xzf "$ort_archive" -C "$work_dir"
sudo mkdir -p /usr/local/include/onnxruntime
sudo cp -r "$work_dir/$ort_dir/include/." /usr/local/include/onnxruntime/
sudo cp -r "$work_dir/$ort_dir/lib" /usr/local/
echo "ONNX Runtime $version installed."

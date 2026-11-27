#!/bin/bash
# This script installs sccache on the local machine. Note that we don't install
# sccache on Linux since it's installed elsewhere through all the containers.

set -euo pipefail
IFS=$'\n\t'

source "$(cd "$(dirname "$0")" && pwd)/../shared.sh"

# Note that this script is executed **inside** the source directory.
# So take care not to extract stuff into it that could perturb the build or the distributed
# source tarballs.

if isMacOS; then
    mkdir -p sccache
    cd sccache
    curl -fLo sccache.tar.gz \
      "https://github.com/mozilla/sccache/releases/download/v0.20.1/sccache-v0.20.1-x86_64-apple-darwin.tar.gz"
    echo '9e828824e52e12bd9c164859e893f0fb344cb3811faf7ea708f3c718d108c100  sccache.tar.gz' | shasum -a 256 -c -
    tar -xvf sccache.tar.gz --strip-components 1 \
      sccache-v0.20.1-x86_64-apple-darwin/sccache
    rm sccache.tar.gz
    mv sccache /usr/local/bin/sccache
    chmod +x /usr/local/bin/sccache
elif isWindows; then
    mkdir -p sccache
    curl -fLo sccache/sccache.zip \
      "https://github.com/mozilla/sccache/releases/download/v0.20.1/sccache-v0.20.1-x86_64-pc-windows-msvc.zip"
    echo 'ca500a01139e9af8d5f5a5e69b6a0dd941ed79ed3399a91cc35bba484fe732fa  sccache/sccache.zip' | sha256sum -c -
    unzip -j sccache/sccache.zip sccache-v0.20.1-x86_64-pc-windows-msvc/sccache.exe -d sccache
    rm sccache/sccache.zip
    ciCommandAddPath "$(cygpath -m "$(pwd)/sccache")"
fi

# FIXME: we should probably install sccache outside the containers and then
# mount it inside the containers so we can centralize all installation here.

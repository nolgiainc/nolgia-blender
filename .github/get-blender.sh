#!/usr/bin/env bash
# Download Blender 4.2 LTS (the oldest Blender the extension supports) into
# $RUNNER_TEMP, outside the source folder, and export BLENDER for later steps.
set -euo pipefail
VERSION=4.2.23
SHA256=bea0eb3146be13eae6225409a117b215184f41b7f79e799f97cb3abb8f6dc404
dir="$RUNNER_TEMP/blender"
mkdir -p "$dir"
curl -fsSL --retry 3 -o "$RUNNER_TEMP/blender.tar.xz" "https://download.blender.org/release/Blender4.2/blender-$VERSION-linux-x64.tar.xz"
echo "$SHA256  $RUNNER_TEMP/blender.tar.xz" | sha256sum -c -
tar -xJf "$RUNNER_TEMP/blender.tar.xz" -C "$dir" --strip-components=1
rm "$RUNNER_TEMP/blender.tar.xz"
sudo apt-get update -qq
sudo apt-get install -y -qq libxxf86vm1 libxfixes3 libxi6 libxkbcommon0 libsm6 libgl1 libegl1 >/dev/null
"$dir/blender" --version | head -1
echo "BLENDER=$dir/blender" >> "$GITHUB_ENV"

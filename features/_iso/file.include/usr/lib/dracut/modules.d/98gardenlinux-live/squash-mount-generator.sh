#!/bin/bash

command -v getarg >/dev/null || . /lib/dracut-lib.sh

set -e

GENERATOR_DIR="$1"

mkdir -p /run/rootfs

cat >"${GENERATOR_DIR}/run-rootfs.mount" <<EOF
[Unit]
DefaultDependencies=no
Before=sysroot.mount
After=dracut-initqueue.service

[Mount]
What=/run/root.squashfs
Where=/run/rootfs
Type=squashfs
Options=loop
EOF

mkdir -p "$GENERATOR_DIR"/initrd-root-fs.target.requires
ln -s ../run-rootfs.mount "$GENERATOR_DIR"/initrd-root-fs.target.requires/run-rootfs.mount

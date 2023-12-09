#!/bin/bash

name=${1}
shift 1

if [ ${#} -eq 0 ]; then
        set -- /bin/bash --login
fi

(bwrap --die-with-parent \
        --unshare-uts --unshare-ipc --unshare-ipc --unshare-cgroup-try --share-net \
        --hostname ${name} \
        --bind /data/arthurzam/chroot/${name} / \
        --bind /var/cache/distfiles /var/cache/distfiles \
        --bind /srv/tattoo /srv/tattoo \
        --ro-bind /var/db/repos/gentoo /var/db/repos/gentoo \
        --ro-bind /etc/resolv.conf /etc/resolv.conf \
        --dev /dev \
        --proc /proc \
        --perms 1777 --tmpfs /dev/pts \
        --perms 1777 --tmpfs /dev/shm \
        --perms 1777 --tmpfs /tmp \
        --perms 1777 --tmpfs /run \
        --info-fd 11 \
$@) \
        11>${HOME}/${name}.json

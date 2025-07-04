#!/bin/bash

source /lib/gentoo/functions.sh || :

die() {
	echo "$@" >&2
	exit 1
}

[[ ${PWD} == /srv/tattoo/systemd ]] || die "Please run this script from /srv/tattoo/systemd"

read -p "enter arch (add ~ as prefix for unstable): " ARCH
[[ -n "${ARCH}" ]] || die "ARCH cannot be empty"
read -p "enter -j value for MAKEOPTS (in range [1-$(nproc)]): " JOBS
(( JOBS >= 1 && JOBS <= $(nproc) )) || die "Invalid value for JOBS: ${JOBS}"
read -p "enter -l value for MAKEOPTS (in range [1-${JOBS}]): " LOAD
(( LOAD >= 1 && LOAD <= JOBS )) || die "Invalid value for LOAD: ${LOAD}"
EMERGE_JOBS=$(( JOBS >= 16 ? 4 : JOBS >= 4 ? JOBS / 4 : 1)) # value is between 1 and 4
EMERGE_LOAD=$(( LOAD > 1 ? LOAD - 1 : 1 ))


einfo "Installing systemd service"
ln -vsf /srv/tattoo/systemd/tester.service /etc/systemd/system/tattoo.service || die "Failed to link tattoo.service"
mkdir -p /etc/systemd/system/tattoo.service.d || die "Failed to create tattoo.service.d directory"
cat > /etc/systemd/system/tattoo.service.d/override.conf <<-EOF || die "Failed to create override.conf"
	[Service]
	Environment=ARCH=${ARCH}
EOF
systemctl daemon-reload || die "Failed to reload systemd daemon"

einfo "Enabling systemd service"
systemctl enable tattoo.service || die "Failed to enable tattoo.service"

einfo "Installing systemd-tmpfiles files"
mkdir -p /etc/tmpfiles.d || die "Failed to create /etc/tmpfiles.d directory"
ln -vsf /srv/tattoo/systemd/tmpfiles-portage-tmpdir.conf /etc/tmpfiles.d/portage-tmpdir.conf || die "Failed to link tmpfiles configuration"


einfo "Preparing for simple pkgdev usage"
ln -vsf /srv/tattoo/bugs.key ~/.bugz_token || die "Failed to link bugs.key to ~/.bugz_token"
mkdir -p ~/.config/pkgdev/ || die "Failed to create ~/.config/pkgdev directory"
cat > ~/.config/pkgdev/config <<-EOF || die "Failed to create pkgdev config"
	[DEFAULT]
	tatt.test =
	tatt.use-combos = 1
	tatt.use-default =
EOF

einfo "Preparing /etc/portage"
if [[ -d /etc/portage/profile/package.use.force ]]; then
	ewarn "Directory /etc/portage/profile/package.use.force already exists, removing it"
	rm -rfv /etc/portage/profile/package.use.force || die "Failed to remove /etc/portage/profile/package.use.force"
fi
ln -vsf /srv/tattoo/profile/* /etc/portage/profile || die "Failed to link profile files"

cat >> /tmp/make.conf <<-EOF || die "Failed to append to /etc/portage/make.conf"

	# tattoo settings
	MAKEOPTS="-j${JOBS} -l${LOAD}"
	EMERGE_DEFAULT_OPTS="--nospinner --ask-enter-invalid --quiet-build --keep-going --complete-graph --with-bdeps=y --load-average ${EMERGE_LOAD} --deep --jobs=${EMERGE_JOBS}"
	PORTAGE_IONICE_COMMAND="ionice -c 3 -p \\\${PID}"
	PORTAGE_NICENESS=11
	PORTAGE_ELOG_CLASSES="qa"
	#GENTOO_MIRRORS="https://gentoo.osuosl.org/"
	CLEAN_DELAY=0
	ACCEPT_LICENSE="*"
	L10N="en"
	LINGUAS="en"
	PORTAGE_ELOG_SYSTEM="echo save"
	PORTAGE_LOGDIR="/var/log/portage"
	FEATURES="${FEATURES} split-elog split-log -merge-sync parallel-install parallel-fetch -news"
	PORTAGE_LOG_FILTER_FILE_CMD="bash -c \\"ansifilter; exec cat\\""
EOF

einfo "Done! Now perform @world upgrade with emerge"

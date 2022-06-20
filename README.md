# Project name origin

Well, naming things is very hard. Thankfully we have an excellent team at
Gentoo capable of naming things - The Infra team. Alec Warner (antarus)
was kind enough to decide on the name "tattoo", which came from combining
"tatt" (package testing tool used here) and "Gentoo".

# Setup

## Developer's own machine

1. Install the following dependencies:
    * `app-portage/nattka`
    * `net-misc/openssh`
2. Select the directory from which we will work. Always run and set files
    inside this directory. It can be the source files directory.
3. Create a `ssh_config` file using the `ssh_config.in` template. Fill your
    username, select the wanted path for socket on every host, and define
    the various hosts and theirs hostname.
4. Add an environment variable (for example into `.bashrc`) named
    `ARCHTESTER_BUGZILLA_APIKEY` which equals to an API key from bugzilla.

## Remote machine - machine manager

1. Install the following dependencies:
    * `dev-lang/python[sqlite(+)]` (tested on >=3.9)
    * `app-portage/nattka`
2. Select the directory from which we will work. Always run and set files
    inside this directory. It can be the source files directory. It must be the
    same directory as set in the ssh_config directory (by default `~/tattoo`)

## Remote machine - testing container

1. Install the following dependencies:
    * `app-portage/nattka`
    * `app-portage/tatt`
2. Make sure the working directory of the machine manager is mount bound into
    the container. The mount destination inside the container would be the
    working directory for the testing container.
3. Create a corresponding `~/.tatt` file inside container, for example
   (*IMPORTANT*: replace ARCH, and set templates directory pointing to tattoo's
   tatt templates)
    ```
    arch=arm64
    emergeopts="--autounmask --autounmask-continue --autounmask-write"
    repodir="/var/db/repos/gentoo/"
    ignoreprefix="elibc_","video_cards_","linguas_","python_targets_","python_single_target_","kdeenablefinal","test","debug","qemu_user_","qemu_softmmu_","libressl","static-libs","systemd","sdjournal","eloginid","doc","ruby_targets_"
    buildlogdir=/root/logs
    rdeps=0
    usecombis=1
    template-dir=/root/tattoo/tatt-templates
    ```
    The special tatt templates are used for creating machine readable report
    files, which tattoo can parse and send more specific error messages.

# Running and using

## Load all remote machines

1. Run in selected directory (for example `~/tattoo`) the command
    `./manager.py`.
    * Inside this directory a file named `tattoo.socket` will be created.
        Through this socket all communication will occur.
    * A SQLite DB named `tattoo.db` will hold all successes and failures
        of test runs.
2. In every container on that machine, run the command
    `./tester.py -n [NAME] -a [ARCH] -j [JOBS]` where `NAME` is just a nice
    textual name to know which container did what, `ARCH` is the arch to test,
    with `amd64` for stable bugs, and `~arm` for keyword bugs. `JOBS` is the
    maximal concurrent testing jobs.
    * This command must be ran in the mount bound dir from manager, where the
        `tattoo.socket` is created (so it can communicate).
3. Check that the `manager` logs all containers connecting to it.

## Control from developer's own machine

1. Connect to remote servers listed in `ssh_config` using `./controller.py -c`.
    Various sockets are created inside `/tmp/tattoo/` directory
2. Send specific bugs using `./controller.py -b {NUM} {NUM} ...` or initiate
    full scan for open bugs per arch using `./controller.py -s`
3. When bugs are ready, use `./controller.py fetch -n` to view all done bugs,
    but in dry-run mode (no update for bugzilla, and no update last-seen bugs).
    Btw, the output corresponds to sam's `at-commit` script.
4. When ready to apply, run `./controller.py fetch -ar -d [REPO]` where `REPO`
    is the ::gentoo repo to apply on it the commits. This command also un-CC
    and closes bugs for what passed. After success, it saves in small file the
    last seen bugs, so you don't try to reapply them.
5. From `REPO` push the commits (if you are unlucky, `git pull --rebase` before)
6. Send, fetch, apply how much you want
7. Disconnect from all using `./controller.py -d`

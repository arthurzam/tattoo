# Setup

This guide shows how to setup full tattoo service based with shared containers,
on a systemd based devbox. Note that this needs that the containers are also
systemd based.

I recommend to setup shared Arch Testing containers, so only one instance of
`tattoo` will run on each devbox. It can easily overload the whole devbox by
itself, and having two might just halt the devbox.

As root on host system:

1. `mkdir -p /srv/tattoo && cd /srv/tattoo`
2. `git clone https://gitlab.gentoo.org/arthurzam/tattoo.git .`
3. Setup testing containers in `/var/lib/machines` and setup the corresponding
   `/etc/systemd/nspawn/*.nspawn` config files base on the template.
4. Enable the containers to auto start on boot `machinectl enable [name]`
5. Enable the all machines on boot: `systemctl enable machines.target`
6. If you want to setup max resources for all containers combined
   (recommended), use `systemctl edit machine.slice` and set:
   ```
   [Slice]
   CPUAccounting=true
   CPUQuota=300%
   ```
   Here `300%` represents maximum load of 3 cores. You can do `1600%` for max
   16 cores. For more info read `man 5 systemd.resource-control`.
7. Copy the `manager.service` into `/etc/systemd/system/tattoo.service`
8. (Optional) To use systemd socket activation, copy `manager.socket` into
   `/etc/systemd/system/tattoo.socket`, and enable it by running:
   `systemctl enable tattoo.socket`. On first access to this socket, it wil
   auto start the manager.

Open a shell in each container:

8. Copy (and edit as needed) the `tester.service`.
9. Check that you have configured `/root/.tatt` file, especially the
   `template-dir` field to point to `/srv/tattoo/tatt-templates`


# Usage

All those commands are from the host system - all of them need root.

- Starting the manager (if not using socket activation):

  `systemctl start tattoo.service`
- Starting a tester in container named `amd64-stable`:

  `systemctl -M amd64-stable start tattoo.service`
- Viewing log of manager:

  `journalctl -f -u tattoo.service`
- Viewing log oftester in container named `amd64-stable`:

  `journalctl -M amd64-stable -f -u tattoo.service`
- Stopping manager (and all testers connected to it):

  `systemctl stop tattoo.service`

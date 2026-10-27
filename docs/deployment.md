# Deploying to AWS Lightsail

End to end: create the instance, make an SSH key, copy the code, run it as a
service behind nginx.

The port plan's Phase 10 was a PyInstaller + Inno Setup Windows installer for a
Qt desktop app. There is no desktop app, so this is the equivalent for a web
service.

---

## This deployment

The walkthrough below is generic and uses `SERVER_IP` throughout. The live
instance is:

| | |
|---|---|
| URL | **https://sbfsem-tools.com** |
| Static IP | **<server-ip>** |
| Plan | General purpose, **4 vCPU, 16 GB RAM, 320 GB SSD**, dual-stack (`sbfsem-web-upgrade`; DNS moved here 2026-09-12 05:20 UTC; the 2 vCPU / 3.8 GB instance at <old-server-ip> is retired) |
| TLS | Let's Encrypt (ECDSA), webroot renewal via `certbot.timer` |
| Region | us-west-2 (Oregon) |
| Blueprint | Ubuntu 24.04 LTS |
| Login user | `ubuntu` |
| SSH key | `~/.ssh/sbfsem-lightsail` (ed25519) |
| Application | `/srv/sbfsem/app` |
| OData cache | `/srv/sbfsem/cache` |
| Service | `sbfsem-web.service`, uvicorn on 127.0.0.1:8765 behind nginx |

The IP is static, so it survives stop/start and is safe to put in DNS or a
bookmark. If the instance is ever rebuilt from a snapshot, re-attach this same
static IP to the new instance rather than taking a new address.

### Everyday commands

```bash
# shell in
ssh -i ~/.ssh/sbfsem-lightsail ubuntu@<server-ip>

# deploy a change -- tests, syncs, restarts, verifies, restores state
./deploy/redeploy.sh

# is it up (public)
curl -fsS https://sbfsem-tools.com/api/health

# the same thing by hand, if you want the steps separately
./deploy/sync.sh ubuntu@<server-ip>
ssh -i ~/.ssh/sbfsem-lightsail ubuntu@<server-ip> \
    'sudo bash /srv/sbfsem/app/deploy/bootstrap.sh'

# ad-hoc file transfer
sftp -i ~/.ssh/sbfsem-lightsail ubuntu@<server-ip>

# is it up, from the box itself (the app port: no nginx, no TLS, no redirect).
# Not `curl http://<server-ip>/...` from outside: since TLS, port 80 answers with
# a 301 to the https name, which `curl -f` reports as success with nothing read.
ssh -i ~/.ssh/sbfsem-lightsail ubuntu@<server-ip> 'curl -fsS http://127.0.0.1:8765/api/health'
```

Adding this to `~/.ssh/config` shortens all of the above to `ssh sbfsem`,
`sftp sbfsem` and `./deploy/sync.sh sbfsem`:

```
Host sbfsem
    HostName <server-ip>
    User ubuntu
    IdentityFile ~/.ssh/sbfsem-lightsail
    IdentitiesOnly yes
```

`IdentitiesOnly yes` matters if you have several keys in `~/.ssh`: without it
SSH offers them all and can exhaust the server's `MaxAuthTries` before reaching
the right one.

---

## What the app needs

Measured on the real workload — loading neurons from Viking, fitting the IPL
boundary surfaces, building cylinder and closed-curve renders.

### Memory

Measured on the original instance (Ubuntu 24.04, 4 GB, 2 vCPU):

| | Resident |
|---|---|
| Service idle, just started | **101 MB** |
| Two neurons loaded with synapses | 122 MB |
| After a cylinder mesh (75,720 faces) | 145 MB |
| After the IPL boundary fit | **124 MB** — *lower*, see below |
| After a closed-curve volume render (359,092 faces) | **176 MB** |

That is the whole working session inside 176 MB of 3.7 GB.

The drop across the boundary fit is `malloc_trim` doing its job. The fit
allocates hundreds of megabytes for a sparse solve over 40,000 unknowns and
produces 0.7 MB of surface; glibc would keep those arenas for the life of the
process, so the service hands them back explicitly.

Earlier development measurements on macOS showed a 385 MB floor and a 730 MB
peak. macOS has no `malloc_trim` equivalent, so those numbers carried all of
that retained heap — the sizing below was set against them and is therefore
conservative by roughly a factor of four.

### CPU

A mesh build is about two seconds and **single-threaded** — CPU time equals wall
time. Only the sparse solve behind the boundary fit uses more than one core
(1.5x), and that runs once per volume and is then cached.

So a single render never benefits from extra cores. Concurrency is where cores
matter, and there the picture is sharp:

| Concurrent renders | In one process (threads) | In separate processes |
|---|---|---|
| 1 | 0.43 req/s | 0.48 req/s |
| 2 | 0.81 req/s | 0.92 req/s (1.9x) |
| 4 | 0.77 req/s — **no gain, latency doubles** | 1.84 req/s (3.8x) |
| 8 | 0.63 req/s — **worse than 4** | 3.47 req/s (7.2x) |

The work is genuinely CPU-parallel; the GIL is what stops threads using it. The
mesh path is Python-loop-heavy — a Nelder-Mead solve per annotation — so it holds
the GIL for most of its runtime.

The build queue is sized by three environment variables in the unit file (the same queue runs renders, error maps and mesh differences; each finished job is logged with its kind, cell, settings and duration, so `journalctl -u sbfsem-web | grep "job .* done"` is the measurement of how long they take):

| Variable | Meaning | 4 vCPU / 16 GB (production since 2026-09-11) | The old 2-vCPU box |
|---|---|---|---|
| `SBFSEM_MESH_WORKERS` | builds in flight at once (threads) | 4 | 2 |
| `SBFSEM_MESH_MEMORY_MB` | estimated peak memory the running builds may share; a build waits until it fits, and one heavier than the whole budget runs alone | 9000 | 1700 (`MemoryHigh` minus the ~600 MB idle footprint) |
| `SBFSEM_MESH_PROCESSES` | run the geometry step in this many child processes instead of the calling thread | 4 | 0 (threads still give the 1.9x at two) |

Not in the unit file, optional: `SBFSEM_MESHLAB_TIMEOUT_S` (default 1800) is how long a MeshLab remesh may run before its child process is killed and the job ends in an error. No HTTP request waits on it, because renders, error maps and mesh differences are jobs.

Viking's section correction (`sbfsem.io.corrections`) is applied to every
loaded cell in the mode `SBFSEM_SECTION_CORRECTIONS` names, unless its panel
asks otherwise: `curvefit` by default since 2026-09-24 (the user's decision;
the service's curve fit along each process), except for the Neitz lab's
volumes, which take `SBFSEM_SECTION_CORRECTIONS_NEITZ` (`all`, the field then
the fit, since 2026-10-20, the user's decision); `neighbor` (the residual field;
`1` means this, and it was the default on 2026-09-22 until it measured worse
on 2026-09-23), `all`, or `off`. A `CorrectStructures` call takes 5-10 s for
a cell of 1,700 annotations and is kept on disk with the service's
`AnswerStamp`; each later load of the cell asks `GetAnswerStamp` (about 0.1 s)
and asks the fit again only when the stamp moved: a new service version, a
rebuilt field (`all`), or an annotation of the cell added, changed or deleted.
A restart does not move it. An exception
inside the service on one cell's request (gRPC `UNKNOWN`, `INTERNAL` or
`INVALID_ARGUMENT`) holds that cell and mode for ten minutes, and the whole
volume and mode once two different cells of it fail so in a row (RC1 threw
after 5-15 s on every call on 2026-09-24; on 2026-10-20 Cped's volume server
refused connections on and off, and every Cped request failed meanwhile). Any
other error from the service holds the volume and mode at once, and an
unreachable service is left alone for two minutes, for every volume (one whose
correction sets were asked for in the last ten minutes too: until 2.17.5 such a
volume was still asked, and against a host that drops traffic each load waited
out its 120 s deadline); a cell whose offsets are on disk is corrected from
them meanwhile, and its status says so. When the service answers
`UNAVAILABLE` naming the volume asked about (it could not reach that volume's
own server: "volume mapping temporarily unavailable for 'cped'"), only that
volume is left alone for two minutes, in Curve fit and Field + fit together
(the one call that maps its sections); the other volumes are still asked.
`SBFSEM_CORRECTIONS_URL` names the service (default
`https://corrections.codepharm.net`; an `http://` URL means cleartext
HTTP/2), and `SBFSEM_CORRECTIONS_STOS_GROUP` picks a set by name (default: the
first by name, as Viking's DataExport picks). None of them is set in the
unit. The offsets are kept in `SBFSEM_CACHE_DIR` beside the OData responses.
The journal logs every correction applied (`section correction … moved`) and
every failure (`section corrections unavailable`). The client speaks the
service's protocol V2 (`Viking.SectionCorrectionServiceTypes.gRPC.V2`) since
2.39.1. V1 was retired without notice: from 2026-10-26 15:13 UTC every V1 call
answered `UNIMPLEMENTED Service is unimplemented`, logged as `section correction
(…) not applied: ListCorrectionSets … UNIMPLEMENTED`, and every cell loaded
until 2.39.1 was drawn uncorrected. A run of those lines means the protocol
moved again: `tools/section_corrections.py` with no arguments shows what the
service answers.

Cells are drawn where each volume's current section transforms put them
(`sbfsem.io.registration`). The transforms are read over plain HTTP from the
hosts that serve Viking's volumes: `v2486.host.s.uw.edu` (Inferior Monkey),
`v2223.host.s.uw.edu` (Cped) and `rogue1.codepharm.net` (RPC1, RPC2). The
VikingXML and stos files are kept in `SBFSEM_CACHE_DIR`. The journal logs each
cell's moves (`current section transforms: … moved`) and any failure
(`live registration unavailable`). `SBFSEM_LIVE_REGISTRATION=0` draws the
stored positions instead; it is not set in the unit.

`SBFSEM_UNTAGGED_PSD` (not set in the unit) chooses what a postsynapse
whose Viking tags do not say it is ribbon or conventional becomes: `infer`
(the default) takes the linked partner's kind or keeps it as `UntaggedPost`;
`separate` never infers; `assume` counts it conventional, as MATLAB did.

Because of the GIL, threads stop paying beyond two concurrent builds — the
table above is the measurement, and why the process pool is on now. The
sizing rule, from [vm-upgrade.md](vm-upgrade.md): set
`SBFSEM_MESH_PROCESSES` and `SBFSEM_MESH_WORKERS` to the vCPU count, set
`SBFSEM_MESH_MEMORY_MB` to about 70 % of RAM minus 800 MB minus 250 MB per
child process, and `MemoryHigh`/`MemoryMax` to ~70 % / ~85 % of RAM; each
child process costs ~250 MB idle on top of its build. A child process
still holding more than `SBFSEM_MESH_RECYCLE_MB` (1024) after a build, its heap
trimmed, is let go with its pool and the next build goes to a new one: without
that, one build of Inferior c1178 at Face cap 3,400,000 left its worker at 2.8 GB
for as long as the service ran (2026-10-17). A decimation of 200,000 triangles or
more runs in a short-lived child of the build process (`sbfsem.geometry.quadric`),
because `fast_simplification` never gives back what it copied: after the same build a
worker now holds 181 MB, not 452 (2.19.8). A Voxel remesh, Convolution, Ultraliser or
Hybrid build hands its freed heap back to the system (`malloc_trim`) once its field is
made and again between the smoothing and the decimating, so neither step runs beside
what the last one freed (2.20.1). The unit file carries
the 16 GB numbers, so do not redeploy it onto a 3.8 GB box unchanged. The *Viking fetch* side of a batch load —
the part that dominates for cells not yet cached, at several seconds each —
is network-bound and already runs in parallel with no setting to change.

### Effect on other users

| Server state | Median latency of a cheap request | p95 |
|---|---|---|
| Idle | 11 ms | 13 ms |
| One render in flight | 12 ms | 308 ms |
| Two renders in flight | **180 ms (16x)** | 468 ms |

One render is effectively invisible to everyone else. Two makes the whole
interface feel broken. That is the number to design around.

### Disk

About 500 MB for the virtual environment, 15 MB for the repository, plus the
OData response cache, which grows a few MB per neuron and never expires (the label and vessel lists inside it are asked again after an hour), and
the built-mesh cache under it (`meshes/`, one `.npz` per cell × render
options × annotation stamp; under a megabyte for a typical cell, ~25 MB for
a 1.4M-face vessel), pruned least-recently-used past `SBFSEM_MESH_CACHE_MB`
(8 GB in the unit file). At 1,090 cached responses the cache was 1 GB on a
77 GB disk; the disk is not the constraint.

---

---

## 1. Create the instance

In the Lightsail console, **Create instance**.

### Region

Pick the region closest to the people using it. Note that the Viking OData
service the backend queries is hosted at the University of Utah, so a US region
keeps the import path short. `us-west-2` (Oregon) is a good default.

### Platform and blueprint

Select **Linux operating system** — not "Linux apps". You want a bare OS; the
Bitnami-packaged application blueprints are being deprecated anyway (the banner
at the top of that page says so), and none of them match this stack.

For the blueprint, **Ubuntu 24.04 LTS** is the recommendation over the
pre-selected Amazon Linux 2023:

- Ubuntu 24.04 ships Python 3.12 as `python3`. This package needs ≥ 3.11.
  Amazon Linux 2023's default `python3` is 3.9, so you need
  `dnf install python3.12` and then have to remember to build the virtual
  environment with `python3.12` rather than `python3`.
- Supported until April 2029.

`deploy/bootstrap.sh` handles both, so Amazon Linux 2023 will work if you prefer
it — it detects the distribution and installs `python3.12` explicitly.

### SSH key pair

This is where you make the key. In the **SSH key pair** section, choose
**Change SSH key pair**.

Lightsail offers to generate one for you and hand you a `.pem` download. Prefer
**Upload new** instead, with a key you generate locally — that way the private
key is never transmitted, and you get a modern ed25519 key rather than Lightsail's
RSA.

Generate it first, on your Mac:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/sbfsem-lightsail -C "sbfsem-lightsail"
```

Press Enter twice to skip the passphrase, or set one — if you set one, add it to
the agent once per login with `ssh-add ~/.ssh/sbfsem-lightsail` so `rsync` and
`ssh` do not prompt on every call.

That writes two files:

- `~/.ssh/sbfsem-lightsail` — the private key. Never leaves your machine.
- `~/.ssh/sbfsem-lightsail.pub` — the public key. This is what you upload.

Then, under **Advanced settings → SSH key**, click **Upload key** and choose
`~/.ssh/sbfsem-lightsail.pub`. Not **Create custom key**, and not the
**Default SSH key** in the dropdown:

- **Create custom key** has Lightsail generate the pair and hand you a `.pem`.
  The private half is created on AWS's side and downloaded to you, you get
  exactly one chance to save it, and it will be RSA rather than ed25519.
- **Default SSH key** is a per-region key Lightsail made for you. Since you have
  never downloaded its private half, selecting it locks you out of your own
  instance.

Uploading a key you generated means the private half never leaves your machine,
you choose the algorithm, and the same key works across every instance and region
you create later.

If the file picker will not show hidden folders, press `Cmd+Shift+.` to reveal
`~/.ssh`.

Lightsail has historically been fussy about ed25519 on the upload path. If it
rejects the key, fall back to RSA — still generated locally, still never
transmitted:

```bash
ssh-keygen -t rsa -b 4096 -f ~/.ssh/sbfsem-lightsail -C "sbfsem-lightsail"
```

### Instance plan

Choose **Dual-stack** for the network type. IPv6-only is cheaper but many home
and campus networks still cannot reach an IPv6-only host, and you would not be
able to SSH in from one.

**Plan type: General purpose.** Not Compute-optimized, despite the rendering —
a mesh build is single-threaded, so extra cores do nothing for one render, and
the current single-worker service cannot use more than about two of them anyway.
Not Memory-optimized either: at roughly 8 GB per vCPU it buys memory this
workload does not need. General purpose's ~2 GB per vCPU is close to the ~1 GB
per worker this actually wants. (Once you are running several workers for
multiple users, Compute-optimized becomes the better ratio — see below.)

Then the size. Given a 385 MB floor, a ~730 MB peak, and a two-render ceiling:

| Plan | Verdict |
|---|---|
| $5 — 512 MB | No. The boundary fit alone does not fit. |
| $7 — 1 GB | No. Survives startup, dies on the first render. |
| $12 — 2 GB | Cylinder renders and one or two neurons only. No headroom for a closed-curve volume. |
| $24 — 4 GB, 2 vCPU, 80 GB | What ran from 2026-08-29 to 2026-09-11. The 2 vCPUs match the two-concurrent-render ceiling exactly. In practice a full session uses 176 MB, so the memory is mostly headroom — see the note below. |
| $44 — 8 GB, 2 vCPU | More memory, same two cores. Only worth it if you raise the closed-curve resolution above the 0.05 µm default. |
| **$84 — 16 GB, 4 vCPU, 320 GB** | **What is running** since 2026-09-11: four mesh processes, room for two large builds to overlap. The reasoning is in [vm-upgrade.md](vm-upgrade.md). |

80 GB of SSD and 4 TB of transfer are both far beyond what this needs — a mesh
response is 2–12 MB, so 4 TB is several hundred thousand of them. RAM is the
only real constraint.

A new AWS account gets a free trial period on some sizes, which is what the
"Free credits for new AWS accounts" notice refers to.

Resizing later is a snapshot, a new instance from it, and re-attaching the static
IP — about fifteen minutes. Do not over-buy now to avoid that.

**In hindsight, on the measured Linux numbers, $12 (2 GB) would carry a
single-user deployment comfortably** — 176 MB of working set against 2 GB. The
4 GB plan is still the right place to be if the multi-user work lands, since each
additional worker costs its own baseline, and both plans have the same 2 vCPU so
there is no throughput difference to reclaim by downgrading.

### Observed timings on this instance

| | |
|---|---|
| First load of a neuron from Viking | ~14 s |
| The same neuron from the OData disk cache | **0.1 s** |
| Cylinder mesh, 75,720 faces | 4.5–7.6 s |
| Closed-curve volume, 359,092 faces | 9.5 s |
| `.glb` / `.stl` / `.dae` / `.obj` export of an already-built mesh | 0.2–0.5 s |
| Mesh JSON over the wire, gzipped by nginx | 2.2 MB → 640 KB |
| Redeploy (sync + bootstrap, no package changes) | 13 s |

Mesh builds are roughly twice as slow as on an M4 laptop, which is what a
shared 2 vCPU instance should look like.

### Launch script

Optional, and worth using. **Add launch script** and paste
[`deploy/launch-script.sh`](../deploy/launch-script.sh).

It only installs the packages `bootstrap.sh` needs, turns on automatic security
updates, and pre-creates `/srv/sbfsem` owned by `ubuntu` so the first `rsync`
works without a permissions dance. Two minutes of first-boot work you would
otherwise wait through.

It deliberately does **not** touch sshd, the firewall or user accounts. A launch
script runs exactly once and cannot be edited afterwards, so anything that could
lock you out belongs in `bootstrap.sh`, which you can re-run.

It also waits for the apt lock before installing — cloud-init races Ubuntu's own
daily apt timers on first boot, and without the wait the first `apt-get` usually
dies on a held dpkg lock.

If it fails, the log is at `/var/log/sbfsem-launch.log` and
`/var/log/cloud-init-output.log`. Nothing in it is required — `bootstrap.sh`
installs the same packages and is idempotent — so a failure costs you two
minutes, not the instance.

### Name and create

Names cannot be changed after creation, so encode the **role**, not the
implementation: `sbfsem-web` rather than `sbfsem-pytools-ubuntu`. The OS is the
part most likely to change — you will rebuild on a newer Ubuntu eventually, and
a resize means creating a fresh instance from a snapshot — and a name that
disagrees with the box is worse than a generic one.

If you expect a second instance later, `sbfsem-web-prod` leaves room for
`sbfsem-web-staging`.

Then **Create instance**. It takes a minute or two to reach *Running*.

---

## 2. Give it a static IP

Do this before anything else. A Lightsail instance's default public IP changes
every time it stops and starts, which would break your SSH config and any DNS
record.

Console → **Networking** tab → **Create static IP** → attach it to the instance.
It is free while attached to a running instance.

Note the address; it is used everywhere below as `SERVER_IP`. For this
deployment it is **<server-ip>** — see *This deployment* above.

---

## 3. Open the firewall

Console → the instance → **Networking** tab → **IPv4 Firewall**.

| Application | Protocol | Port | Restrict to |
|---|---|---|---|
| SSH | TCP | 22 | **your IP address** |
| HTTP | TCP | 80 | Anywhere, or your institution's range |
| HTTPS | TCP | 443 | Anywhere (only if you set up TLS in step 7) |

Restrict SSH to your own address using the "Restrict to IP address" option —
leaving 22 open to the world invites constant brute-force attempts.

**Do not open 8765.** The application binds to `127.0.0.1` only; nginx is what
faces the network.

---

## 4. Confirm you can log in

```bash
chmod 600 ~/.ssh/sbfsem-lightsail
ssh -i ~/.ssh/sbfsem-lightsail ubuntu@SERVER_IP
```

The login user is `ubuntu` on the Ubuntu blueprint and `ec2-user` on Amazon
Linux 2023.

Worth adding to `~/.ssh/config` so you can type `ssh sbfsem` from then on:

```
Host sbfsem
    HostName SERVER_IP
    User ubuntu
    IdentityFile ~/.ssh/sbfsem-lightsail
    IdentitiesOnly yes
```

---

## 5. Copy the code across

From the repository root on your machine:

```bash
./deploy/sync.sh ubuntu@SERVER_IP ~/.ssh/sbfsem-lightsail
```

Or, with the `~/.ssh/config` entry above, just `./deploy/sync.sh sbfsem`.

The script uses `rsync`, so re-running it after an edit sends only what changed.
It excludes `.venv/` deliberately — a virtual environment contains
platform-specific compiled wheels and must be rebuilt on the target, not copied
from macOS.

If you would rather see the raw command, it is:

```bash
ssh -i ~/.ssh/sbfsem-lightsail ubuntu@SERVER_IP \
    'sudo mkdir -p /srv/sbfsem/app && sudo chown -R $USER:$USER /srv/sbfsem'

rsync -az --delete --info=stats1,progress2 \
    -e "ssh -i ~/.ssh/sbfsem-lightsail" \
    --exclude '.venv/' --exclude '__pycache__/' --exclude '*.pyc' \
    --exclude '.pytest_cache/' --exclude '.ruff_cache/' --exclude '.mypy_cache/' \
    --exclude '.git/' --exclude 'build/' --exclude '*.egg-info/' \
    --exclude '.DS_Store' --exclude '.coverage' --exclude '.claude/' \
    ./ ubuntu@SERVER_IP:/srv/sbfsem/app/
```

That moves about 15 MB, most of it the recorded Viking cassettes and the MATLAB
ground truth under `tests/`. To skip those on a production box, add
`--exclude 'tests/'` — but then you cannot run the test suite on the server to
confirm the install, which is worth doing at least once.

### If you would rather use git

```bash
ssh sbfsem
sudo mkdir -p /srv/sbfsem && sudo chown -R $USER:$USER /srv/sbfsem
git clone <your remote> /srv/sbfsem/app
```

Updates then become `git -C /srv/sbfsem/app pull`. `rsync` is the better fit
while the code is not yet in a remote repository.

---

## 6. Bootstrap the server

```bash
ssh sbfsem
sudo bash /srv/sbfsem/app/deploy/bootstrap.sh
```

That script is idempotent — re-run it after every sync. It:

1. installs Python, a compiler toolchain and nginx for your distribution;
2. checks the interpreter is ≥ 3.11 and fails loudly if not;
3. creates an unprivileged `sbfsem` service account;
4. builds `/srv/sbfsem/venv` and installs the package with its `web` extras;
5. verifies `sbfsem` imports and its packaged data files (the XY offset tables
   and the cached IPL boundary markers) are present;
6. installs and starts the systemd unit and the nginx site;
7. polls `/api/health` until the service answers.

It ends by printing the health response. Then open `http://SERVER_IP/` in a
browser.

The first request that needs IPL depth takes about a second longer while the
boundary surfaces are fitted; they are cached for the life of the process.

---

## 7. HTTPS

**A Lightsail or ACM certificate cannot be attached to a Lightsail instance.**
AWS never releases the private key for those, so they only work on a Lightsail
load balancer (~$18/month) or a CDN distribution. For a single instance the
certificate has to live on the box, which means Let's Encrypt.

Point an A record at the static IP, deploy so nginx serves the ACME challenge
path, then:

```bash
sudo bash /srv/sbfsem/app/deploy/certbot-setup.sh sbfsem-tools.com you@example.com
```

Only **port 80** needs to be open for the certificate to be issued — the
HTTP-01 challenge runs over it. Open 443 afterwards.

Then redeploy. `bootstrap.sh` installs `nginx-sbfsem-tls.conf` instead of
`nginx-sbfsem.conf` whenever a certificate exists for `$SITE_DOMAIN`, so TLS
survives every future deploy:

```bash
./deploy/redeploy.sh
```

### Why not `certbot --nginx`

The nginx plugin rewrites `/etc/nginx/sites-available/sbfsem` in place, and
`bootstrap.sh` reinstalls that file from the repository on every deploy. The
plugin's work would be silently reverted at the next redeploy and the site would
drop back to plain HTTP. `certbot-setup.sh` uses the **webroot** method instead:
it touches nothing nginx owns, so the two coexist. The renewal path
(`/.well-known/acme-challenge/`) is served from `/var/www/certbot` by both
configs and is deliberately exempt from the HTTPS redirect.

`deploy/nginx-sbfsem-tls.conf` is generated — edit `nginx-sbfsem.conf` and run
`python3 deploy/render-nginx-tls.py`, so the proxy and gzip rules cannot drift
between the two.

### Renewal

The certbot package installs `certbot.timer`, which runs twice daily and renews
inside 30 days of expiry. A deploy hook reloads nginx afterwards — without it
nginx keeps serving the old certificate until something else restarts it. Check
it with:

```bash
sudo certbot renew --dry-run
```

A `rateLimited: Service busy` result is Let's Encrypt throttling, not a
misconfiguration; wait and retry.

### HSTS

`Strict-Transport-Security` starts at `max-age=86400`. A long max-age is hard to
undo — every browser that has seen it refuses plain HTTP for that period even if
the certificate lapses. Raise it to `31536000` in `render-nginx-tls.py` once
renewal has run cleanly in production.

### Who can get in

Only signed-in people get past the sign-in pages; `/api/health` and
`/api/build` are the only other paths that answer without a sign-in. The
built-in accounts are described under [Access control](#access-control) below;
signing in with a Viking Identity account, which also limits the volumes a
person sees to the ones Viking grants them, is in
[Viking Identity sign-in](viking-identity.md). Both work at once. Requests from
the server's own loopback (the deploy script, health checks) bypass the login.

---

## Operating it

From your machine, one read-only command gathers the usual first look —
build stamp, service state, memory against its cgroup ceilings and the
kernel's memory-pressure counters, the loaded neurons, the service journal,
and nginx's error log and status-code histogram:

```bash
./deploy/status.sh                  # the report
./deploy/status.sh --errors --since -2h
./deploy/status.sh --stack          # adds a py-spy dump of the worker
```

On the server itself:

```bash
sudo systemctl status sbfsem-web        # is it running
sudo journalctl -u sbfsem-web -f        # follow the logs
sudo systemctl restart sbfsem-web       # after a code change
sudo tail -f /var/log/nginx/sbfsem.error.log
```

An admin (`SBFSEM_ADMIN_USERS`) can do three of these from the app's **Settings** tab, the
last tab: clear the cached renders (in memory, or in memory and on disk), replace the
build processes, and restart the service. The restart ends the process with exit code 75
after a normal shutdown, and the unit starts it again for that code
(`RestartForceExitStatus=75`) without logging it as a failure (`SuccessExitStatus=75`);
a server systemd did not start refuses it. Every one of them is written to the journal
with who asked. The same tab shows anyone which build their page is against the one the
server serves (ground rule 7), clears the browser's cached files of the site
(`Clear-Site-Data: "cache"`), and resets what the browser saved for the site.

### Access control

The app sits behind a login whenever `/srv/sbfsem/auth/users.json` exists;
with no such file it runs open (which is how a local checkout and the test
suite behave). Maintain the user list with the bundled CLI — changes take
effect immediately, no restart:

```bash
sudo -u sbfsem /srv/sbfsem/venv/bin/sbfsem-users --dir /srv/sbfsem/auth invite alice  # they pick their own password
sudo -u sbfsem /srv/sbfsem/venv/bin/sbfsem-users --dir /srv/sbfsem/auth add alice     # you type their password
sudo -u sbfsem /srv/sbfsem/venv/bin/sbfsem-users --dir /srv/sbfsem/auth remove alice  # signs them out at once
sudo -u sbfsem /srv/sbfsem/venv/bin/sbfsem-users --dir /srv/sbfsem/auth list
```

`invite` is the usual way in: it prints a one-time `/welcome?code=...` link,
good for 7 days (`--days` to change). Send it to the person; opening it shows
a "choose a password" page, and submitting signs them in on the spot. The
code is stored hashed and is spent on first use, so the link works exactly
once — re-run `invite` to issue another (which also serves as a password
reset, even for an active account). `add` remains for accounts where you set
the password yourself, such as a shared `guest` login.

Passwords are stored as salted PBKDF2 hashes, never in plain text. Sessions
are signed cookies (30 days) keyed by `/srv/sbfsem/auth/session.key`; delete
that file to sign everyone out at once. Removing the *last* user locks the
site rather than opening it — to turn the login off entirely, delete
`users.json` itself. Requests from the server's own loopback (the deploy
script, health checks) bypass the login. nginx-forwarded traffic never appears
as loopback, so this is not reachable from outside -- as long as every proxied
`location` in the nginx config sets `X-Forwarded-For` itself, which is what
`tests/unit/test_deploy_files.py` holds both configs to: until 2026-10-16 the
`/static/vendor/` location passed a client's own header through, and a request
for a vendored library claiming to come from `127.0.0.1` skipped the login.

### MeshLab remeshing on the server

`deploy/bootstrap.sh` installs pymeshlab (the optional `[meshlab]` extra;
GPL-3, about 300 MB in the venv) and the two system libraries its wheel links
(`libgl1`, `libopengl0`; no display is needed), then proves it by running one
small mesh through the worker. All of it is best effort: a failure prints
`MeshLab remeshing NOT available on this host` and the deploy carries on, the
app graying the control out (`/api/build` reports `"meshlab": false`).
`SBFSEM_MESHLAB=0` skips the step.

### Redeploying

`./deploy/redeploy.sh` is the one command to use after editing anything,
backend or frontend:

```bash
./deploy/redeploy.sh                     # the normal case
./deploy/redeploy.sh --skip-tests        # you just ran them
./deploy/redeploy.sh --full              # the full bootstrap.sh, even for a code-only change
./deploy/redeploy.sh --no-restore        # do not re-load the server's cells (browsers restore their own)
./deploy/redeploy.sh --restore-jobs 8    # re-load that many cells at a time (default 4)
```

It runs the local checks first (pytest, ruff on `src` and `tests`, and mypy:
the project's ground rule 4) and refuses to deploy a failing build, or from a
machine with no `.venv` to run them in (`--skip-tests` ships anyway).  With
pytest-xdist installed (the `dev` extra) the suite runs in `SBFSEM_TEST_WORKERS`
processes, 6 by default.  Then it snapshots which neurons the server has loaded,
syncs, and runs `bootstrap.sh --quick`.  That compares the synced tree's install
stamp (`deploy/install_stamp.py`: the dependencies in `pyproject.toml` without
its version line, the unit, the nginx configs and `bootstrap.sh` itself) with the
one its last full run left in the venv: the same, and it reinstalls only the
package (`pip --no-deps`) and restarts the service; different, or no stamp yet,
and it does the full run.  So a new directory or dependency always gets the full
run (ground rule 10 changes the unit and `bootstrap.sh`): 13 s for the full run,
5 s quick, on the 2.6.4 deploys.  Then it waits for health, re-loads the
snapshotted neurons four at a time (which pays when cells must ask Viking for
their section correction again, and is a little slower from the disk cache: 342
cells took 67 s against 51 s one by one), warns if the assets did
not actually change -- which is what a silently-no-op sync looks like -- and
prints how long each step took.

**Open browsers look after themselves.**  Each tab polls `/api/build` every 20
seconds.  When the version, the asset hash or the process instance id changes,
the tab saves its session, shows a banner, and offers a reload that restores
it.  Nothing needs to be announced to users, and no one has to be told to
hard-refresh.

### Environment variables

Every variable the code reads. The production unit
(`deploy/sbfsem-web.service`) sets the first block; the rest have working
defaults, and go in the unit or in `/etc/sbfsem/secrets.env` (the unit's
`EnvironmentFile`, which a redeploy does not overwrite).

**Where things live** (set in the unit; the defaults are for a checkout)

| Variable | Default | What |
|---|---|---|
| `SBFSEM_CACHE_DIR` | `$XDG_CACHE_HOME/sbfsem`, else `~/.cache/sbfsem` | OData cache and, under `meshes/`, built renders |
| `SBFSEM_SAVE_DIR` | `~/.sbfsem/saves` | saved workspaces (Save / Open) |
| `SBFSEM_CELL_RENDER_DIR` | `~/.sbfsem/cell-renders` | renders attached to cells |
| `SBFSEM_LIBRARY_DIR` | `~/.sbfsem/library` | shared materials and gradients |
| `SBFSEM_AUTH_DIR` | `~/.sbfsem/auth` | `users.json`, the session key, Viking sessions; login is on iff `users.json` exists |
| `SBFSEM_ADMIN_USERS` | none | user names (built-in or Viking, comma-separated, any case) offered the Settings tab's server tools: clear cached renders, replace the build processes, restart the service. With no sign-in configured everyone is offered them, and so is any request from the machine itself |

A directory the service writes must be in the unit's `ReadWritePaths=`, created
and chowned (and `chmod 750`) in `deploy/bootstrap.sh`, and named by one of
these (CLAUDE.md ground rule 10).

**Builds** (set in the unit, sized for the 16 GB box; scale all of them with the box)

| Variable | Default | What |
|---|---|---|
| `SBFSEM_MESH_WORKERS` | 1 | builds that run at once (job threads) |
| `SBFSEM_MESH_PROCESSES` | 0 | child processes the geometry step runs in; 0 builds in the job thread |
| `SBFSEM_MESH_RECYCLE_MB` | 1024 | a child process still holding more than this after a build (its heap trimmed) is let go with its pool, and the next build goes to a new one whose first worker is started at once; 0 never replaces one |
| `SBFSEM_MESH_MEMORY_MB` | 1700 | estimated peak memory the running builds may add up to (the build gate) |
| `SBFSEM_MESH_CACHE_MB` | 4096 | built renders kept on disk, pruned least recently used |
| `SBFSEM_MESH_CACHE_SIZE` | 12 | built renders kept in memory (a blank, zero or invalid value is the default) |
| `SBFSEM_GRID_CACHE_MB` | 512 | MB of whole-cell grids kept in memory for the manual moves' Apply, only for cells move mode was started on or that have moves (21 MB for Inferior c1178 under Voxel remesh, 45 MB under Hybrid; the least recently used goes first; 0 keeps none, and every Apply builds the whole cell) |
| `SBFSEM_MESHLAB_TIMEOUT_S` | 1800 | a MeshLab remesh not back by then is killed |

**Behavior**

| Variable | Default | What |
|---|---|---|
| `SBFSEM_OFFLINE` | off | `1`: never ask Viking, serve what the cache holds (a development server) |
| `SBFSEM_UNTAGGED_PSD` | `infer` | what a postsynapse with no kind in its Viking tags becomes: `infer`, `separate` or `assume` |
| `SBFSEM_SECTION_CORRECTIONS` | `curvefit` | Viking's section correction a load gets when it names none: `off`, `neighbor`, `curvefit`, `all` |
| `SBFSEM_SECTION_CORRECTIONS_NEITZ` | `all` | the same for the Neitz lab's volumes (Inferior, Nasal, Temporal, Cped) |
| `SBFSEM_CORRECTIONS_URL`, `SBFSEM_CORRECTIONS_STOS_GROUP` | built in | the correction service and the stos group it reads |
| `SBFSEM_LIVE_REGISTRATION` | on | `0`: draw cells at their stored positions, not through the volume's current section transforms |
| `SBFSEM_LOG_LEVEL` | `INFO` | the level of this application's own log lines (uvicorn's are its own) |
| `SBFSEM_PUBLIC_URL` | `https://sbfsem-tools.com` | the address `sbfsem-users invite` prints in an invite link |

**Viking sign-in** (off unless `SBFSEM_VIKING_AUTH` and the client secret are set; see [Viking Identity sign-in](viking-identity.md))

| Variable | Default | What |
|---|---|---|
| `SBFSEM_VIKING_AUTH`, `SBFSEM_VIKING_CLIENT_SECRET` | off, none | `1`, and the secret (from `/etc/sbfsem/secrets.env`, never the unit) |
| `SBFSEM_VIKING_AUTHORITY`, `SBFSEM_VIKING_PERMISSIONS_API`, `SBFSEM_VIKING_CLIENT_ID`, `SBFSEM_VIKING_SCOPES` | the identity server's | where to sign in, whom to ask for grants, and as whom |
| `SBFSEM_VIKING_REDIRECT_URI`, `SBFSEM_VIKING_POST_LOGOUT_URI` | `https://sbfsem-tools.com/auth/callback`, `https://sbfsem-tools.com/` | where the identity server sends the browser back, after signing in and after signing out |
| `SBFSEM_VIKING_GATE_VOLUMES` | on | `0`: sign people in without limiting the volumes they see |
| `SBFSEM_VIKING_LAUNCH_MODE`, `SBFSEM_VIKING_LAUNCH_URL`, `SBFSEM_VIKING_LAUNCH_MAX_DOWNSAMPLE` | `direct`, the identity server's `CreateCode`, 32 | how "Open in Viking" hands a place to the client |

**The deploy scripts** read `SBFSEM_HOST` and `SBFSEM_KEY` (where to ssh, and with
which key; `redeploy.sh` and `status.sh`), `SBFSEM_URL` (the public address
`redeploy.sh` prints), `SBFSEM_TEST_WORKERS` (how many pytest-xdist processes
`redeploy.sh` tests in, default 6, about 1.2 GB each) and, in `bootstrap.sh`,
`SBFSEM_MESHLAB=0` (skip the MeshLab install).

### What survives a restart, and what does not

| | Survives | Why |
|---|---|---|
| Annotations from Viking | yes | on-disk OData cache in `/srv/sbfsem/cache` |
| Which neurons are loaded | yes* | re-loaded by `redeploy.sh` from its snapshot |
| Each user's neurons, colors, per-cell render settings, camera | yes | browser `localStorage`, restored on reload |
| Built meshes | yes | on-disk cache under `/srv/sbfsem/cache/meshes`, keyed by cell, render options and the annotation stamp the data carried |

Re-loading a neuron after a restart costs ~0.1s because the annotations are
cached, and re-meshing it at settings it has been built with before is a
disk read.  Only a render at new settings, or of a cell Viking has edited
since, is built again.

**Deploying a change** by hand is two commands from your machine:

```bash
./deploy/sync.sh sbfsem
ssh sbfsem 'sudo bash /srv/sbfsem/app/deploy/bootstrap.sh'
```

**Confirming the install** on the server. `bootstrap.sh` installs only the `web`
extras, so the test runner is not there by default — add it once:

```bash
sudo /srv/sbfsem/venv/bin/pip install pytest pytest-cov
cd /srv/sbfsem/app && /srv/sbfsem/venv/bin/python -m pytest tests -q
```

That runs offline against the recorded cassettes and the captured MATLAB ground
truth, so it is a real check that the deployment computes the same numbers — not
just that the service starts.

**Checking memory:**

```bash
systemctl show sbfsem-web -p MemoryCurrent
```

The unit sets `MemoryHigh=11000M` and `MemoryMax=13500M` (~70 % / ~85 % of
the 16 GB box). `MemoryHigh` throttles and
reclaims before `MemoryMax` kills, so a runaway render degrades rather than
taking the process down.

---

## Planning for multiple users

You mentioned a login system with several people using this at once. Two things
in the current design will get in the way, and they are related.

### The service is single-tenant

There is one `SbfsemService` for the whole process, created in the app's
lifespan. "Which neurons are loaded" is therefore **global, not per-user**: if
one person loads c1441, everyone's `/api/neurons` lists it, and anyone can unload
it out from under them.

That is fine for one researcher and wrong the moment there are accounts. The fix
is to make the *loaded set* per-session — a list of `(source, id)` per user —
while leaving the neuron data, meshes and boundary surfaces in the shared caches
where they belong. Those are derived deterministically from Viking and are
identical for every user, so sharing them is correct and saves a great deal of
memory.

### One worker caps throughput at two renders

`--workers 1` is in the unit file because the loaded set lives in process memory:
a second worker would hold its own, and a request routed to the wrong one would
404 on a neuron the user had just loaded.

Fixing the first problem removes this constraint. Once the loaded set is in a
session store rather than the process, workers become interchangeable — and a
worker that does not have a neuron in memory can rebuild it from the shared
OData disk cache in **about 60 ms**, so the in-memory cache is a performance
nicety rather than a correctness requirement.

Then raise `--workers` in `deploy/sbfsem-web.service`. The measurements above say
that scales nearly linearly: 3.8x at four processes, 7.2x at eight.

### Sizing once that lands

Budget roughly per worker, plus the shared overhead:

| | |
|---|---|
| Per worker, steady | ~400–500 MB (each process fits its own boundary surfaces) |
| Per concurrent closed-curve render | +350 MB transient |
| OS and nginx | ~300 MB |

For eight users of whom two render at once, four workers is a reasonable target:
`4 × 450 MB + 2 × 350 MB + 300 MB ≈ 2.8 GB`, peaking around 3.5 GB.

At that point **Compute-optimized becomes the better fit than General purpose** —
you want roughly one core and one gigabyte per worker, and Compute-optimized's
ratio is closer to that than General purpose's 2 GB per vCPU or
Memory-optimized's 8 GB. Compare the 4 vCPU options on the Compute-optimized tab
against General purpose's $84 (16 GB / 4 vCPU); you will likely pay less for the
same core count and still have ample memory.

Also raise `SBFSEM_MESH_CACHE_SIZE` (default 12) — that is about four neurons at
three render variants each, which several people would thrash. 40 entries costs
about 170 MB.

### Two more things the login layer will need

- **Sign-in exists; per-person state does not.** Accounts arrived on
  2026-08-30 (built-in) and 2026-09-09 (Viking Identity), but everything above
  still holds: the loaded set, saves and attached renders are shared by every
  signed-in person.
- **Long requests hold a worker.** A closed-curve render occupies its worker for
  seconds. With per-user queuing you may want to reject a second concurrent
  render from the same user rather than let one person saturate the pool.

---

## Things worth knowing

**The mesh cache is bounded** at 12 entries, evicted least-recently-used. Each
distinct combination of render options is its own entry, so dragging the decimate
slider creates one per position; without the bound a long-running server grows
without limit.

**The OData cache is shared and a cell's rows never expire.** (The lists that grow -- label searches, the vessel and cone lists, the Graffinity start set -- are asked again after an hour.) It lives at
`/srv/sbfsem/cache`, so a second user loading the same neuron does not re-query
Viking. To force a refresh, pass `refresh: true` when loading a neuron, or delete
the directory.

**Running fully offline.** With a populated cache, set `SBFSEM_OFFLINE=1` in the
systemd unit. Any uncached URL then fails with a clear error instead of reaching
the network — useful for a demo machine or for reproducing an analysis after the
database has moved on.

**Snapshots.** Lightsail can take manual or automatic snapshots of the whole
instance from the console. Worth enabling automatic snapshots once you have the
OData cache warm, since rebuilding it means re-querying Viking.

# Suggested changes to the Lightsail VM

A summary for deciding whether, and how, to enlarge the server. Written
2026-09-09 against the instance as it is; the numbers are measured, not
guessed. The runbook for the rest of the deployment is
[deployment.md](deployment.md).

> **Done, 2026-09-11.** The recommendation below was taken: instance
> `sbfsem-web-upgrade`, General purpose 4 vCPU / 16 GB / 320 GB, dual-stack,
> static IP <server-ip>. The settings in *What to change after resizing*
> are in `deploy/sbfsem-web.service`, and `deploy/redeploy.sh` and
> `deploy/status.sh` default to the new address. "The instance today"
> below describes the 2 vCPU box it replaced.

## The instance today

| | |
|---|---|
| Plan | Lightsail, **2 vCPU, 3.8 GB RAM, 77 GB disk** (72 GB free) |
| Service ceiling | `MemoryHigh=2600M`, `MemoryMax=3300M`; idle RSS ~560 MB |
| Build queue | `SBFSEM_MESH_WORKERS=2` threads, `SBFSEM_MESH_MEMORY_MB=1700`, `SBFSEM_MESH_PROCESSES=0` |
| Caches | OData responses ≈ 1 GB; built meshes under `cache/meshes`, capped at `SBFSEM_MESH_CACHE_MB=8192` |

## What a bigger VM would and would not speed up

**Loading many cells by label** has two halves.

1. *Fetching from Viking* — several seconds per cell the first time, ~0.1 s
   from the cache afterwards. This is network-bound and already runs six
   cells at a time, with the server no longer serializing fetches. **More
   CPU or RAM does nothing for it.**
2. *Building meshes* — 0.05–0.6 s for a typical bipolar cell, seconds to
   tens of seconds for a large ganglion cell, ~80 s for a vessel volume on
   this box. This is CPU-bound and is what a bigger VM helps.

The catch, measured earlier and recorded in deployment.md: mesh builds hold
Python's GIL for most of their runtime, so **threads stop paying past two
concurrent builds** (2 threads: 1.9×; 4 threads: no gain and double the
latency). Separate processes scale (4: 3.8×; 8: 7.2×). The process pool is
already implemented (`SBFSEM_MESH_PROCESSES`); it is off in production only
because on 3.8 GB there is no room for it — each worker process costs
~250 MB idle on top of its build, and one closed-curve build can peak at
2.5 GB.

**Disk does not need to grow**, though it will anyway. 72 GB free today;
the mesh cache prunes itself at 8 GB and the OData cache is 1 GB after a
year of use. The 320 GB that comes with the recommended plan is not a
reason to move, but it does buy a much larger mesh cache — see
[what to change](#what-to-change-after-resizing) below.

## Recommendation

A **4 vCPU / 16 GB** Lightsail plan is the sweet spot: four builds at once
(a label load of small cells goes ~3.5× faster than today's two threads),
enough RAM that two large builds can overlap, and headroom to stop worrying
about the memory ceiling. 8 vCPU / 32 GB buys more concurrency than one
lab's usage will fill. Anything with less than 8 GB does not justify the
move: the process pool cannot be turned on.

In the Lightsail console that is **General purpose, Dual-stack, $84/month**
-- 4 vCPU, 16 GB, 320 GB SSD, 6 TB transfer.

### Why General purpose and not the other two tabs

The three plan types are three memory-to-vCPU ratios: general purpose gives
4 GB per vCPU, compute-optimized 2 GB, memory-optimized 8 GB. The measured
footprint picks the middle one. Budgeting from the per-worker figures in
[deployment.md](deployment.md#sizing-once-that-lands), at 4 vCPU running
four web workers beside four mesh processes:

| | |
|---|---|
| Baseline (4 × 450 MB + 4 × 250 MB + 300 MB for OS and nginx) | ~3.1 GB |
| Realistic peak, four concurrent closed-curve renders | ~4.5 GB |
| Worst case, one 2.5 GB closed-curve render beside three others | ~6.7 GB |

*Compute-optimized* at 4 vCPU gives 8 GB, which is exactly the floor above:
a 6.7 GB worst case in an 8 GB box leaves no margin, and reaching 16 GB
means buying 8 vCPU — paying for cores this workload will not fill.
*Memory-optimized* at 4 vCPU gives 32 GB, about five times the worst case,
at a premium per vCPU. General purpose is the only tab whose ratio matches
the numbers.

Skip the 8 GB / **2 vCPU** plan for the same reason as any other 2 vCPU
plan: it clears the memory floor but leaves parallelism where it is today,
so it costs more and changes nothing.

### Keep Dual-stack, not IPv6-only

Nothing here confirms that Viking's OData endpoint answers on IPv6. On an
IPv6-only instance every fetch would lean on the DNS64/NAT64 gateway, which
puts an extra failure mode in front of the one dependency the cache cannot
cover. Institutions still on IPv4-only networks would also lose the site.

### General purpose instances are burstable

They have a sustainable CPU baseline and accrue burst capacity while idle,
which is the right shape for a server that is not always rendering. A long
batch — a few hundred cells in one go — can exhaust that capacity and
drop the instance to its baseline. Watch CPU utilization and burst capacity
in the Lightsail console after the first large label load. If builds
throttle there, the answer is the compute-optimized tab, which is not
burstable, rather than a larger general purpose plan.

## Ten to twenty users at once

Four vCPUs are still the right number, and the arithmetic is not close.
Four cores supply 240 core-seconds per minute, so for twenty users
saturation arrives when

    mean build seconds / minutes between builds per user = 12

Against the build times measured above:

| Workload | Build | Per user | Ratio |
|---|---|---|---|
| Small bipolar cells | 0.05–0.6 s | every minute | 0.6 |
| Large ganglion cell | ~20 s | every 5 minutes | 4 |
| Vessel volume | ~80 s | every 10 minutes | 8 |

Every row sits under 12, and two effects pull the real load lower still.
`MeshJobStore.submit` shares queued, running and finished jobs by key, so
ten people opening the same cell build it once; the mesh cache then makes
every later view of it free. In a lab those are the common case rather than
the edge case, because people look at the same cells.

**The VM is not what caps this.** deployment.md records that the service is
[single-tenant](deployment.md#the-service-is-single-tenant): one
`SbfsemService` per process, so the loaded-neuron set is global, and
`--workers 1` is pinned in the unit file because of it. With ten to twenty
people that is a correctness problem — one person unloads a cell out from
under everyone else — and no plan size fixes it. The per-session refactor
described there is the prerequisite. The 4 vCPU / 16 GB box is sized
correctly for the app on both sides of that work, since the 6.7 GB worst
case above already assumes four web workers.

## What to change after resizing

Lightsail cannot resize an instance in place: take a snapshot, create the
new instance from it on the larger plan, then move the static IP across.
Starting at 4 vCPU / 16 GB therefore does not foreclose 8 vCPU / 32 GB
later.

The settings are all in `deploy/sbfsem-web.service`; redeploy with
`./deploy/redeploy.sh`.

| Setting | Rule | 4 vCPU / 16 GB | 8 vCPU / 32 GB |
|---|---|---|---|
| `SBFSEM_MESH_PROCESSES` | = vCPUs | 4 | 8 |
| `SBFSEM_MESH_WORKERS` | = vCPUs | 4 | 8 |
| `SBFSEM_MESH_MEMORY_MB` | ≈ 70 % of RAM − 800 MB − 250 MB × processes | 9,000 | 19,000 |
| `MemoryHigh` | ≈ 70 % of RAM | 11,000M | 22,000M |
| `MemoryMax` | ≈ 85 % of RAM | 13,500M | 27,000M |
| `SBFSEM_MESH_CACHE_MB` | disk you are willing to give renders | 65,536 | 65,536 |

The cache row is much larger than the old one because the disk goes from
77 GB to 320 GB. At 64 GB the mesh cache effectively stops evicting, and
for several people working over an overlapping set of cells that matters
more than any of the four cores: a cache hit costs nothing, a job already
running costs nothing, and only a rebuild costs a core.

Once those are in, confirm with `./deploy/status.sh`: the health line should report the
new `workers` and `memory_mb`, and `/prod`'s memory pressure should stay at
0.00 during a label load of thirty cells.

## Things that are not about the VM

- Viking's own response time bounds the first load of any cell; the cache
  removes it thereafter, and cells now report when Viking has edited them.
- The service is still single-tenant (one loaded-neuron table for every
  browser). A bigger box does not change that; a per-session refactor would.

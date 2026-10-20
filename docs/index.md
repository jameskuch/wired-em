# SBFSEM-pytools

A Python port of [SBFSEM-tools](https://github.com/sarastokes/SBFSEM-tools), the
MATLAB connectomics toolbox for serial block-face electron microscopy
annotations from the [Viking](https://connectomes.utah.edu/) OData service.

- **[Installation](installation.md)** — get it running
- **[User guide](user-guide.md)** — the web interface, tab by tab
- **[Backend guide](backend.md)** — using `sbfsem` from Python
- **[Migration guide](migration.md)** — a call-by-call map from MATLAB
- **[HTTP API](api.md)** — endpoints and payloads
- **[Deploying to Lightsail](deployment.md)** — AWS setup, SSH keys, file transfer, systemd
- **[Deviations](deviations.md)** — where this port differs from MATLAB, and why

## Start here

If you have used the MATLAB toolbox, read
**[Deviations](deviations.md)** before comparing numbers. `util/micron2ipl.m`
built the INL interpolation grid from the Y point vector on both axes, which
corrupted every IPL depth the MATLAB toolbox produced through that path. Several
other analyses — tortuosity, the stratification histogram, dendrite segmentation,
mitochondrial volume, closed-curve render scale — were also wrong in ways that
changed their answers rather than crashing.

## Architecture

```
                        Viking OData
                             |
                    sbfsem.io  (httpx, paginating, caching)
                             |
                   sbfsem.core  (Neuron, Structure, types)
                    /        |        \
        sbfsem.geometry  sbfsem.analysis  sbfsem.network
                    \        |        /
                      sbfsem.export
                             |
         sbfsem.service.facade.SbfsemService  (pydantic DTOs)
                             |
                       sbfsem_web  (FastAPI + browser)
```

The dependency runs one way. `sbfsem` never imports `sbfsem_web`, a GUI toolkit,
or anything needing a display; a test asserts it.

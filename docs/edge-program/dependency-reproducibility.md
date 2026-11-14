# Dependency and offline installation notes

`requirements-lock.txt` pins the recorded Python environment. CI also uses the `setup-python` pip cache and runs `pip-audit`. The CI artifact `dependency-inventory.json` reports each locked package, the installed version, available license metadata, and missing/version-mismatch counts. It does not certify license compatibility; any `UNKNOWN` entries require manual review.

To prepare a local wheel cache while online:

```powershell
python -m pip download --requirement requirements-lock.txt --dest .cache/wheelhouse
```

With a compatible wheelhouse already present, install without contacting an index:

```powershell
python -m pip install --no-index --find-links .cache/wheelhouse --requirement requirements-lock.txt
python -m pip install --no-index --find-links .cache/wheelhouse --no-deps -e .
```

The wheelhouse is machine/platform specific for packages containing native extensions. Prepare and verify separate caches for each target OS and Python ABI. The repository does not commit binary wheels or proprietary model weights. A clean, disconnected installation has not yet been executed, so offline reproducibility remains unverified.

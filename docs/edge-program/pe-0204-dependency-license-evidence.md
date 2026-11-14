# PE-0204 dependency and license evidence

Added a lockfile/environment inventory with per-package pinned/installed versions and available license metadata. CI saves machine-readable JSON in the platform coverage artifacts. Wheelhouse download and offline install commands are documented; CI caches pip artifacts.

CI run [#596](https://github.com/crakashp2905-hub/Financial-Brain/actions/runs/43600108119) passed in full on Ubuntu and Windows at commit `a12e10820d534f37454adc8293f891bf828d0ed7`. Both artifacts were uploaded, and the dependency audit and inventory-generation steps passed on both platforms. The inventory parser now understands uv export's Python and platform markers; three focused inventory tests and Ruff pass locally. The old local mismatch counts came from an unrelated environment and are withdrawn. License identifiers still require a reviewed compatibility assessment; this evidence does not claim licenses are compatible.

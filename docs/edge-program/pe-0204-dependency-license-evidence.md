# PE-0204 dependency and license evidence

Added a lockfile/environment inventory with per-package pinned/installed versions and available license metadata. CI saves machine-readable JSON as an artifact. Wheelhouse download and offline install commands are documented; CI caches pip artifacts.

Two inventory tests passed; Ruff and mypy passed. The local environment reports 55 lock entries, 9 missing, 34 version mismatches and 9 unknown/absent license identifiers; it is not a clean reproduction. No license compatibility or offline installation success is claimed.

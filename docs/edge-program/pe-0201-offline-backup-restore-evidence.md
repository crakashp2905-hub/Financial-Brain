# PE-0201 offline backup and restore evidence

The backup helper writes a versioned immutable dependency-set bundle with safe relative paths, file lengths and SHA-256 hashes. Restore verification rejects missing/invalid manifests, traversal, duplicate names, missing or changed files, and empty bundles. Tests preserve two same-named files in separate directories, copy the bundle to an isolated restore path, delete the source tree and verify the recovered bytes.

Verification: 14 session reliability tests passed; Ruff and mypy passed. This validates explicitly declared files. Automatic dependency discovery and one-command reconstruction of a complete real session remain outstanding.

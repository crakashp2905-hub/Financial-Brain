# Dependency lock and offline wheelhouse

`uv.lock` is the platform-aware lock source. `requirements-lock.txt` is generated from the `dev` extra with hashes and is checked against the lock during CI. The project build backend (`setuptools` and `wheel`) is included in the locked development environment.

Both Ubuntu and Windows CI install dependencies with `--require-hashes`, download binary wheels for that runner, build the project wheel, then create a new virtual environment and install using `--no-index --find-links`. The fresh environment runs `pip check` and imports the installed wheel.

The wheelhouse is OS and Python ABI specific and is generated during CI rather than committed. The clean offline installation is still classified as unverified until the new Ubuntu and Windows CI jobs pass.

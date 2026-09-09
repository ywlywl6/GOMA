# Test-only upstream evaluator snapshot

`normalized_energy_model.py` is byte-for-byte identical to the root file at
GOMA main commit `b4015e465d78a8dbeb25ec9220cfe7c34883a865`.
Its Git blob SHA-1 is `67195293bb88906047ffbcb4c629a812fdb2c350`.
The hash is checked by the test suite. The original MIT license is retained.

Production code loads the two modules from the explicit `--goma-root` checkout,
not this test directory. `tools/fetch_upstream.py` can fetch both pinned source
files and their license into a new directory.

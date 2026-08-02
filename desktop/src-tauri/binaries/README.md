# Sidecar binaries

Release builds place a compiled `searchcar-core` binary here using Tauri's
required target-triple suffix, for example:

- `searchcar-core-aarch64-apple-darwin`;
- `searchcar-core-x86_64-pc-windows-msvc.exe`.

These generated binaries are ignored by Git. The source entry point is
`backend/searchcar_core.py`; `scripts/build_desktop_sidecar.py --mode onefile`
compiles it with Nuitka and copies the target-specific result here.

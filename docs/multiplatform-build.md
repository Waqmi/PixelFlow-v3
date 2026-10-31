# Multi-platform builds

The `v3` workflow builds macOS arm64, macOS x86_64, and Windows x64 from the
same `github.sha`. Pushes to `main` and version tags upload Actions artifacts;
version tags create a GitHub Release only after all three platform jobs pass.
Build files and SHA-256 manifests are artifacts or Release assets, never Git
commits.

Both macOS builds declare macOS 11.0 (Big Sur) as the minimum supported system.
The Tauri bundle metadata and the native build environment use this same
deployment target.

Tag versions use the tag without its leading `v` (for example, `v3.0.1`
builds version `3.0.1`). Main branch builds read the version from the checked-in
Tauri configuration and use that same resolved value in build metadata.

## Intel runner

The Intel build uses GitHub-hosted `macos-15-intel`, so no dedicated Intel Mac
or self-hosted runner registration is required. The job checks `uname -m` is
`x86_64`, installs Rust stable with `x86_64-apple-darwin`, Node 20, Python 3.12,
and the locked Worker dependencies. It runs for pull requests, `main` pushes,
and version tags; a tag Release waits for this build.

## Release limits

macOS artifacts and Windows installers are unsigned. Developer ID signing,
notarization, and Windows signing are not configured. CI smoke tests do not
replace Intel Big Sur or Windows device validation.

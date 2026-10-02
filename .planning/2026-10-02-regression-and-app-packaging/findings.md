# Findings & Decisions

## Requirements
- Run a regression round first; package `.app` only when the gates pass.
- Use the existing onedir Worker resource mapping and Tauri app-only bundle path.

## Research Findings
- `apps/desktop/src-tauri/tauri.conf.json` runs `npm run worker:build && npm run build` before Tauri packaging.
- The configured resource is `worker/dist-ondir/pixelflow-worker`, copied into `Contents/Resources/pixelflow-worker`.
- Existing project notes say default DMG bundling is unreliable; build the app bundle only.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| App-only Tauri bundle | Matches the requested `.app` artifact and existing repository evidence. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| Worker `ping` returns `ready` by design | Treat matching `ready` as ping success; expect `done` for shutdown. |

## Resources
- App bundle: `dist/PixelFlow-v3-beta.app`
- Build source bundle: `/private/tmp/pixelflow-cargo-target/release/bundle/macos/PixelFlow.app`
- Packaged Worker SHA-256: `8fb8f4b972415187e2a41ceb61f58655961eb2b81bcdcb84c039121f1c314d51`

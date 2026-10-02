# Findings & Decisions

## Regression Results
- Frontend `npm run build`: passed.
- `npm run protocol:check`: passed.
- Worker: 191 tests passed, 1 existing skip.
- Rust: 4 tests passed with `CARGO_NET_OFFLINE=true`.
- `git diff --check`: passed.

## Package Results
- Tauri app-only bundle built at `/private/tmp/pixelflow-cargo-target/release/bundle/macos/PixelFlow.app`.
- Delivery copy: `dist/PixelFlow-v3-beta.app`.
- Ad-hoc signature verified with `codesign --verify --deep --strict`.
- Packaged Worker hash: `8fb8f4b972415187e2a41ceb61f58655961eb2b81bcdcb84c039121f1c314d51`.
- Worker smoke passed: startup `ready`, `ping` `ready`, `shutdown` `done`, exit code 0, stderr empty.

## Post-package launch issue
- CUA reproduced the symptom: the previous app with the default visible window exposed a native PixelFlow window; the new package with `visible: false` had no accessible window.
- Root fix: remove the `visible: false` window setting from `tauri.conf.json`, restoring Tauri's default visible startup. The existing theme prepaint and `show()` call remain in source.
- Rebuilt and re-signed the app after the fix.
- Post-fix checks: frontend build, Rust 4-test suite, `git diff --check`, signature verification, and packaged Worker ready/ping/shutdown smoke all passed.

## Launch timing repair
- Restored `visible: false` and moved Vue mounting ahead of asynchronous Tauri listener registration.
- `tauriBridge.ts` now publishes the bridge synchronously, queues worker requests until both event listeners are registered, and replays buffered worker events/requests in order.
- This preserves themed first-paint hiding while allowing `App.vue` `onMounted` to call `show()` reliably.

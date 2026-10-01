# Window Flash Investigation

- Scope: v3 child-window first-paint flash when switching to settings, report, or confirmation windows.
- `styles.css` already sets `html`, `body`, `#app`, `.app-shell`, and child-window shells to the app background, so the remaining white frame is before CSS/DOM paint.
- `tauri.conf.json` has no `backgroundColor` on the main window; runtime `WebviewWindow` calls in `App.vue` also omit it for settings/report/confirm.
- Tauri 2.11 supports `backgroundColor` on both static window config and `WebviewWindow` options. The runtime child-window options are the direct path for newly created windows; CSS alone cannot cover the native pre-paint frame.
- Need keep the code change minimal and aligned with the requested window creation scope.
- Applied an inline `html,body,#app` fallback background in `apps/desktop/index.html`; the parallel window task had already added `backgroundColor: "#f5f7fa"` to the static Tauri window config.
- Validation: `npm run build` passed; JSON parse and `git diff --check` passed for the touched entry/config files.

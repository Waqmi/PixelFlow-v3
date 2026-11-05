#!/usr/bin/env node
// Guards for failure modes that are invisible in a screenshot: content that is
// hidden until JavaScript runs, animations killed by a global rule, and host
// reads that drop lines they cannot decode. Each check names the bug it would
// have caught.
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (relative) => readFileSync(join(root, relative), 'utf8')

const failures = []
const fail = (file, message) => failures.push(`${file}: ${message}`)

const css = read('apps/desktop/src/styles.css').replace(/\/\*[\s\S]*?\*\//g, '')
const app = read('apps/desktop/src/App.vue')
const host = read('apps/desktop/src-tauri/src/main.rs')
const worker = read('worker/pixelflow_core/worker.py')

// 3.13 — a global reduce rule silently disabled every animation while the
// in-app checkbox still said they were on.
for (const match of css.matchAll(/@media\s*\(\s*prefers-reduced-motion:\s*reduce\s*\)\s*\{([^}]*)\}/g)) {
  if (/(^|[},])\s*\*\s*\{/.test(match[1])) {
    fail('styles.css', 'a global @media(prefers-reduced-motion) rule overrides the animation setting')
  }
}

// Only a rule that reaches every element can switch animation off behind the
// user's back; a component disabling its own base animation is normal, as long
// as a variant turns it back on.
const globalSelector = /^(?:\*|html|body|:root|#app|\.app-shell)(?![-\w])/
for (const match of css.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
  const [, selector, body] = match
  if (!/\b(animation|transition)\s*:\s*none\b/.test(body)) continue
  const targets = selector.split(',').map((part) => part.trim())
  if (!targets.every((part) => globalSelector.test(part))) continue
  fail('styles.css', `"${selector.trim().slice(0, 60)}" disables animation for every element`)
}

// 3.12 — the confirm grid was blank because cards started at opacity 0 and
// only became visible once IntersectionObserver fired.
const cardBase = css.match(/\.confirmation-dialog\s+\.confirm-card\s*\{([^}]*)\}/)
if (cardBase && /opacity\s*:\s*0(?![.\d])/.test(cardBase[1])) {
  fail('styles.css', '.confirm-card starts hidden; a missed IntersectionObserver blanks the grid')
}

// 3.6 — .lines().flatten() drops every line the host cannot decode, so the
// request behind it waits for its timeout.
if (/\.lines\(\)\s*\.\s*flatten\(\)/.test(host)) {
  fail('main.rs', 'worker stdout is read with .lines().flatten(); decode failures disappear')
}

// 3.6 — the protocol is UTF-8; the worker must not inherit the ANSI code page.
if (!/_configure_stdio\(\)/.test(worker) || !/encoding="utf-8"/.test(worker)) {
  fail('worker.py', 'stdio is not forced to UTF-8; Chinese protocol lines break on Windows')
}

// 3.8 — an error the host cannot match to a request leaves the UI locked.
if (!/_salvage_request_id/.test(worker)) {
  fail('worker.py', 'parse failures no longer recover request_id; the UI cannot settle them')
}

// 3.9 — restoring a snapshot wrote the old directories back into the form on
// every start, so a new run silently inherited a path nobody chose.
if (/inputRoot\.value\s*=\s*savedResume\.input_root/.test(app)) {
  fail('App.vue', 'startup overwrites the input path from the resume snapshot again')
}

// 3.13 — the checkbox is the only switch; an OS media query overrides it
// without the user being able to tell why.
if (/prefers-reduced-motion/.test(css)) {
  fail('styles.css', 'an OS motion preference is honoured directly, bypassing the checkbox')
}

// 3.15 — silent WebView2 installs look like a hung installer.
const conf = read('apps/desktop/src-tauri/tauri.conf.json')
if (/"webviewInstallMode"[\s\S]{0,120}?"silent"\s*:\s*true/.test(conf)) {
  fail('tauri.conf.json', 'WebView2 installs silently; the install page shows no progress')
}

if (failures.length) {
  console.error('UI guards failed:')
  for (const failure of failures) console.error(`  - ${failure}`)
  process.exit(1)
}
console.log('UI guards passed')

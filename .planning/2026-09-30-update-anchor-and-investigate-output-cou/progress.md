# Progress Log

## Session: 2026-09-30

### Current Status
- **Phase:** 1 - Requirements & Discovery
- **Started:** 2026-09-30

### Actions Taken
- Read the handoff document and applied the requested T1/T2 values: shoe 0.58/0.63, shirt thigh 0.40.
- Bumped the synchronized rule version to v3-composition-4 in Worker, cache, UI, and tests.
- Counted the user directories and traced the V3 output path.
- Added full-batch stale generated-image cleanup in the native V3 engine; retry-only runs are excluded.
- Disambiguated repeated model-folder short names (`素材_01` vs `素材_2_01`) to prevent overwrites.
- Added a regression test for stale output removal and updated anchor expectations.

### Test Results
| Test | Expected | Actual | Status |
|------|----------|--------|--------|
| Anchor + engine focused suite | Pass | 63 tests, OK | pass |
| Current input image count | 173 images | 165 JPG + 8 PNG | observed |
| Existing output image count | Explain excess | 1,831 images; current batch predicts 1,243 | root cause identified |
| Full Worker suite | Existing baseline failure only | 133 tests: 132 pass, 1 pre-existing fixture-directory failure (`test_model_fixture_manifest_is_available_and_rule_classifies_model`) | pass with known unrelated failure |
| Fake-renderer current-batch replay | One output per expected variant | 1,243 outputs before validation, with no model-folder collisions | pass |
| Frontend build | Typecheck and Vite build pass | `npm run build` succeeded | pass |
| Rule version check | UI/cache/Worker agree | all report `v3-composition-4` | pass |

### Errors
| Error | Resolution |
|-------|------------|

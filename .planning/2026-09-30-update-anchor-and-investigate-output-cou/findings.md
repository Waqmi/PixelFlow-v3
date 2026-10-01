# Findings & Decisions

## Requirements
- User explicitly wants the pending T1/T2 update from the handoff document: shoe fill 0.58/0.63 and shirt thigh factor 0.40.
- Any composition-parameter change must bump the shared rule/cache version from v3-composition-3 to v3-composition-4 in Worker, cache, UI, and affected tests.
- User reports the latest shirt-material output count roughly doubled. Inputs: `/Users/weiheng/Desktop/测试/上衣测试素材`; outputs: `/Users/weiheng/Desktop/测试/上衣测试素材主图2`.
- Scope is limited to the requested parameter/version change and the root cause of the doubled output count; do not alter unrelated legacy changes.

## Research Findings
- Existing active plan is a different completed performance task; this task has its own plan directory.
- Repository root for code is `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta`.
- Current input path contains 173 image files: 165 JPG and 8 PNG.
- Current output path contains 1,831 image files plus 7 `.DS_Store` files. Its exact count is 249 source-sized result sets × 7 JPEG variants + 22 PNG transparent sources × 4 = 1,831.
- The current input would produce 173 × 7 + 8 × 4 = 1,243 image files under the same default settings. The 588-file excess is exactly 76 extra source sets × 7 plus 14 extra PNG sources × 4.
- Output category names prove the directory was generated from a broader/different batch: it contains 11 product colour groups, while the current input has 4 product groups. This is stale output contamination from reusing an output directory across batches, not a crop-parameter calculation.
- The native V3 engine reused existing files but had no full-batch stale-output cleanup. It could leave old product/model/VIP/transparent files when the input set or classification changed.
- A second issue existed in V3 short naming: separate model folders with the same non-colour label (for example `模特图` and `模特图2`) both started at `素材_01` while sharing the same output category, so later files overwrote earlier files. A fake-renderer replay on the current 173-source batch produced 1,082 unique files before this fix versus the expected 1,243; after disambiguating folder labels it produced all 1,243.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Use the shared generation path as the fix point for count duplication | Ponytail requires fixing the root cause once instead of adding caller-specific filters. |
| Clean only stale image files under known generated categories on full V3 runs | Preserves incremental reuse and unrelated files; retry runs keep existing outputs. |
| Disambiguate repeated source-folder labels in V3 short names | Prevents model-folder collisions while preserving existing names for the first folder. |

## Issues Encountered
| Issue | Resolution |
|-------|------------|

## Resources
-

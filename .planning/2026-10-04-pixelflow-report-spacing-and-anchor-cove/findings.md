# Findings & Decisions

## Requirements
- Report page needs additional vertical padding; copy action should have no default border.
- Review/rollback page needs horizontal breathing room and borderless content cards.
- 1000-image batch showed fewer than half using anchor composition; root cause must be identified, fixed, and regression-tested.
- Preserve `legacy_engine` observation cycle and the two deleted `models/detection/unused` sidecars.
- Repackage the app after passing frontend, worker, and targeted/full validation.

## Research Findings
- Repository is `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta`; current HEAD is `80b542b` and only planning files are uncommitted.
- Existing V3 render flow prioritizes request plans, decision plans, then typed metadata plans; output reuse can mask new behavior when `force_rerender` is false.
- Prior worker delegation attempts are unavailable because all current worker slots returned quota exceeded.

## Technical Decisions
| Decision | Rationale |
|----------|-----------|
| Inspect `engine.py`, `renderer.py`, `v3_classification.py`, and contracts together | The observed symptom can originate at plan generation, request propagation, mode selection, or renderer fallback |
| Add a focused regression test for the exact fallback trigger before broad batch validation | Prevent a large, slow test from passing without proving the cause is fixed |

## Issues Encountered
| Issue | Resolution |
|-------|------------|
| No fast-context MCP tool is exposed in this runtime | Use `rg`/targeted file reads as the next-best repository search method |

## Resources
- `/Users/weiheng/Desktop/PixelFlow/05_测试素材与输出/护膝`
- `/Users/weiheng/Desktop/测试/1000张上衣` (input only; do not modify)
- `/Users/weiheng/Desktop/PixelFlow/PixelFlow-App-v3-beta/dist/PixelFlow-v3-beta.app`

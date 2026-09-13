# Baseline artifacts

`v2.6/no-model-no-logo-1440/baseline.json` is the tracked baseline record.
Generated image binaries stay local and are ignored because they may contain
user-owned product material; their SHA-256 values are recorded in the JSON.

`v2.6/clean-34375bd/no-model-no-logo-1440/baseline.json` is the reproducible
clean-reference baseline used for v3 Adapter parity checks.

`v2.6/clean-34375bd/matrix.json` records the no-model coverage matrix for the
three output sizes, Logo, 唯品图, and their combined configuration.

`v2.6/clean-34375bd/model-probe.json` records that the configured Pose and
Segmentation model files are discoverable; it is not a model-output baseline.

`v2.6/clean-34375bd/model-enabled-1440/baseline.json` records the first real
Pose/Segmentation output baseline using the four model fixtures.

"""H4c: cProfile of one _side_filled_window call (model 4943x7415 -> 1440x1440)."""

import cProfile
import pstats

from common import MODEL_SOURCES, env
from PIL import Image

from bench_micro import model_plan
from pixelflow_core import renderer as R

print(env())
with Image.open(MODEL_SOURCES[0]) as opened:
    image = opened.convert("RGB")
plan = model_plan(MODEL_SOURCES[0], "1440x1440", (1440, 1440), image.size, True)
R._side_filled_window(image, plan.crop_box, (1440, 1440), plan.evidence["side_fill"])  # warm
profiler = cProfile.Profile()
profiler.enable()
R._side_filled_window(image, plan.crop_box, (1440, 1440), plan.evidence["side_fill"])
profiler.disable()
pstats.Stats(profiler).sort_stats("tottime").print_stats(12)

from benchmark.sweep.modes.go import run_batch as run_go_batch
from benchmark.sweep.modes.py_mp import run_batch as run_py_mp_batch
from benchmark.sweep.modes.py_mt import run_batch as run_py_mt_batch

# Mode -> harness function. Each function takes:
#   queries:   list[dict] (already filtered to the batch we want to run)
#   out_dir:   pathlib.Path (where workers write their trace JSONs)
#   concurrency: int
# Returns: BatchResult (defined in summary.py).
HARNESSES = {
    "py-mt": run_py_mt_batch,
    "py-mp": run_py_mp_batch,
    "go": run_go_batch,
}

# All three modes go through the Pro-plan CLI subprocess transport. Phase-2.5
# moved Go off raw-HTTP/metered-API onto the same path Python uses, by routing
# each Go goroutine through a `python -m benchmark.configs.config_chemcrow_py`
# subprocess. The asymmetry that was deliberate in Phase 1 is gone now —
# this removes a confound from the GIL-vs-fork-vs-goroutine comparison.
PRO_PLAN_MODES = {"py-mt", "py-mp", "go"}

__all__ = ["HARNESSES", "PRO_PLAN_MODES"]

"""Benchmark configurations.

Py: Python tool runtime (this directory).
Go: Go tool runtime (benchmark-go/, runs as a separate binary).

The Python CLI (run.py) only dispatches Py. The Go CLI lives in benchmark-go.
Both produce JSON traces with identical span schema consumed by analysis/.

Registered configs:
  - py                : legacy module, default Haiku, 15 s inter-turn pause,
                        historically used with SEARCH_BACKEND=static. Kept for
                        reproducibility of benchmark/traces/py/.
  - haiku_custom      : agentic tool-use loop, Haiku 4.5 + custom Py chain,
                        5 s inter-turn pause. Superseded by pipeline_haiku;
                        kept reachable for forensic comparison against the
                        agentic-architecture cells under
                        benchmark/results/cell_haiku_agentic/.
  - opus_custom       : agentic tool-use loop, Opus 4.7. Same status as
                        haiku_custom.
  - pipeline_haiku    : Cell 1 of EVAL_PLAN.md (current). Hardcoded
                        web_search -> fetch_url x2 -> summarize -> single LLM
                        call pipeline matching Raj et al.'s LangChain
                        orchestrator. Single llm.generate per query.
  - pipeline_opus     : Cell 2 of EVAL_PLAN.md (current). Same pipeline as
                        pipeline_haiku, Opus 4.7 instead of Haiku.
  - pipeline_haiku_hotpot
                      : Workload 1c (Experiment B). Same pipeline as
                        pipeline_haiku, run on HotpotQA-20 instead of
                        FreshQA-20 to test cost-breakdown generalization.
  - chemcrow_py       : Workload 2 (Phase 1). Chemistry agent on Claude Haiku
                        4.5 driven by claude-agent-sdk (Pro-plan tokens).
                        Three RDKit/PubChem tools: lookup_molecule,
                        smiles_to_3d, compute_descriptors. 20 molecules split
                        medium/heavy to reproduce Raj et al. Fig 2e.
"""
from benchmark.configs import (
    config_haiku_custom,
    config_opus_custom,
    config_pipeline_haiku,
    config_pipeline_haiku_hotpot,
    config_pipeline_opus,
    config_py,
)

class _LazyConfigs(dict):
    """Dict that lazy-imports certain configs on first access.

    Why: chemcrow_py registers @tool decorators at import time. If we eagerly
    `from benchmark.configs import config_chemcrow_py` here, then `python -m
    benchmark.configs.config_chemcrow_py` (used by py-mp / Pro-plan Go modes)
    triggers a DOUBLE import — once via this package, once as __main__ —
    duplicating the MCP tool registrations. The Claude CLI subprocess then
    rejects the duplicated tools and dies with "Command failed with exit code 1".
    Deferring the import to first dispatch avoids the double-decoration.
    """

    _LAZY = {"chemcrow_py": "benchmark.configs.config_chemcrow_py"}

    def __getitem__(self, key):
        if key in self._LAZY and not super().__contains__(key):
            from importlib import import_module

            try:
                self[key] = import_module(self._LAZY[key])
            except ImportError as e:
                raise KeyError(f"{key!r} not available: {e}") from None
        return super().__getitem__(key)

    def __contains__(self, key):
        return super().__contains__(key) or key in self._LAZY


CONFIGS: dict = _LazyConfigs(
    py=config_py,
    haiku_custom=config_haiku_custom,
    opus_custom=config_opus_custom,
    pipeline_haiku=config_pipeline_haiku,
    pipeline_opus=config_pipeline_opus,
    pipeline_haiku_hotpot=config_pipeline_haiku_hotpot,
)

"""Phase-2 ChemCrow concurrency sweep.

Three orchestration modes:
  - py-mt: long-lived ThreadPoolExecutor (the natural Python-MT pattern;
           the GIL-contended one Raj et al. critique).
  - py-mp: subprocess.Popen per query, bounded by threading.Semaphore(N).
           One-query-per-process to mirror the `&` shell-background pattern;
           NOT a long-lived ProcessPoolExecutor.
  - go:    subprocess into the Go binary, which itself does sync.WaitGroup
           + bounded semaphore over the 20 queries.

CLI: see runner.py. Output layout: see summary.py.
"""

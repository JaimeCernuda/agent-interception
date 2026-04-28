"""Paper-quality matplotlib figures for the evaluation chapter.

Re-exports `style` so `from benchmark.plots_paper import style` resolves
cleanly under both runtime and static analysis.

Independent of the live-dashboard plotly module under benchmark/analysis/plots.py.
This package targets static PDF + PNG output suitable for two-column paper layout.

Modules:
  - style.py        : matplotlib rcParams + a single shared color palette
  - make_fig1.py    : cost-breakdown stacked bars (Cell 1, Cell 2, Raj et al.)
  - make_fig2.py    : v1-vs-v2 schema retry split, Py vs Go
  - make_fig3.py    : per-stage Py vs Go scatter

Each make_*.py is runnable as a script (python -m benchmark.plots_paper.make_fig2)
and writes its outputs into benchmark/plots_paper/out/.
"""

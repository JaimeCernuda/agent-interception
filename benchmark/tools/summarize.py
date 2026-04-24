"""LexRank extractive summarizer (sumy).

Matches the paper's summarize() stage. Default: 1 sentence per document (paper's
sentences_count=1). We expose n_sentences as a parameter so callers - especially
Config C (Claude native tool) - can choose.
"""
from __future__ import annotations

from benchmark.obs import Observer, input_hash

_DEFAULT_N = 1


def lexrank_summarize(text: str, obs: Observer, n_sentences: int = _DEFAULT_N) -> str:
    """LexRank top-N sentences. Emits one tool.summarize span."""
    # Lazy import: sumy + NLTK pay a non-trivial import cost.
    from sumy.nlp.tokenizers import Tokenizer
    from sumy.parsers.plaintext import PlaintextParser
    from sumy.summarizers.lex_rank import LexRankSummarizer

    with obs.span(
        "tool.summarize",
        **{
            "tool.name": "lexrank",
            "tool.input_hash": input_hash(text),
            "tool.retry_count": 0,
            "tool.n_sentences_out": n_sentences,
        },
    ) as span:
        _ensure_nltk_punkt()
        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        in_sents = len(list(parser.document.sentences))
        summarizer = LexRankSummarizer()
        summary_sents = summarizer(parser.document, sentences_count=n_sentences)
        out = " ".join(str(s) for s in summary_sents)
        span.set("tool.n_sentences_in", in_sents)
        span.set("tool.output_size_bytes", len(out.encode("utf-8")))
        return out


_nltk_ready = False


def _ensure_nltk_punkt() -> None:
    global _nltk_ready
    if _nltk_ready:
        return
    import nltk

    for pkg in ("punkt", "punkt_tab"):
        try:
            nltk.data.find(f"tokenizers/{pkg}")
        except LookupError:
            nltk.download(pkg, quiet=True)
    _nltk_ready = True

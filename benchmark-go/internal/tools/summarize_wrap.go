package tools

import "github.com/annamonso/agent-interception/benchmark-go/internal/obs"

// Summarize is the instrumented LexRank entry point used by the agent loop.
// Mirrors benchmark.tools.summarize.lexrank_summarize.
func Summarize(text string, nSentences int, o *obs.Observer) string {
	s := o.Start("tool.summarize", map[string]any{
		"tool.name":            "lexrank",
		"tool.input_hash":      inputHash(text),
		"tool.retry_count":     0,
		"tool.n_sentences_out": nSentences,
	})
	defer s.End()

	inSents := SentenceCount(text)
	out := LexRank(text, nSentences)
	s.Set("tool.n_sentences_in", inSents)
	s.Set("tool.output_size_bytes", len(out))
	return out
}

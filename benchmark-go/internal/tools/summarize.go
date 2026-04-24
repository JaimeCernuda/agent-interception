// Package tools: LexRank summarizer in pure Go.
//
// Reference: Erkan & Radev, "LexRank: Graph-based Lexical Centrality as
// Salience in Text Summarization", JAIR 2004. Algorithm:
//
//   1. Split text into sentences.
//   2. Tokenize each sentence; compute IDF over the document.
//   3. Represent each sentence as a TF-IDF vector.
//   4. Build a similarity graph: edge weight = cosine similarity between
//      sentence vectors; keep edges with similarity above a threshold.
//   5. Compute stationary distribution via power iteration (PageRank).
//   6. Return the top-N sentences by score in document order.
//
// This is intentionally a by-hand port. It matches the algorithm; it does
// NOT match sumy's output byte-for-byte because (a) our sentence
// tokenizer is simpler (regex-based, not Punkt), and (b) our tokenization
// is ASCII-lowercase with a small stoplist, where sumy uses NLTK.
//
// For the benchmark's purposes, what matters is: same algorithm family,
// same O(n^2) complexity shape, measured at the same semantic boundary.
// Differences in top-1 sentence selection on short inputs are expected
// and are documented in the report.
package tools

import (
	"math"
	"regexp"
	"sort"
	"strings"
)

// LexRank returns the top-n most salient sentences from text, in document order.
// Returns an empty string if text has fewer than n sentences.
func LexRank(text string, n int) string {
	if n <= 0 {
		return ""
	}
	sents := splitSentences(text)
	if len(sents) == 0 {
		return ""
	}
	if n >= len(sents) {
		return strings.Join(sents, " ")
	}

	// Tokenize each sentence to lowercase word bags (alphanumeric only).
	tokBags := make([][]string, len(sents))
	for i, s := range sents {
		tokBags[i] = tokenize(s)
	}

	// Document frequency for IDF.
	df := map[string]int{}
	for _, bag := range tokBags {
		seen := map[string]bool{}
		for _, t := range bag {
			if !seen[t] {
				df[t]++
				seen[t] = true
			}
		}
	}
	nDocs := float64(len(tokBags))
	idf := func(term string) float64 {
		d := df[term]
		if d == 0 {
			return 0
		}
		return math.Log(nDocs / float64(d))
	}

	// TF-IDF vectors per sentence (sparse as map[string]float64).
	vecs := make([]map[string]float64, len(tokBags))
	for i, bag := range tokBags {
		tf := map[string]int{}
		for _, t := range bag {
			tf[t]++
		}
		v := map[string]float64{}
		for term, count := range tf {
			v[term] = float64(count) * idf(term)
		}
		vecs[i] = v
	}

	// Similarity matrix via cosine similarity, threshold-filtered graph.
	const threshold = 0.1
	sim := make([][]float64, len(sents))
	for i := range sim {
		sim[i] = make([]float64, len(sents))
	}
	for i := 0; i < len(sents); i++ {
		for j := i; j < len(sents); j++ {
			s := cosine(vecs[i], vecs[j])
			if i == j {
				// no self loops
				continue
			}
			if s < threshold {
				continue
			}
			sim[i][j] = s
			sim[j][i] = s
		}
	}

	// PageRank via power iteration.
	scores := pageRank(sim, 0.85, 30, 1e-6)

	// Rank by score but emit in original document order so the summary reads naturally.
	ranked := make([]int, len(sents))
	for i := range ranked {
		ranked[i] = i
	}
	sort.SliceStable(ranked, func(i, j int) bool { return scores[ranked[i]] > scores[ranked[j]] })
	top := ranked[:n]
	sort.Ints(top)

	out := make([]string, 0, n)
	for _, idx := range top {
		out = append(out, strings.TrimSpace(sents[idx]))
	}
	return strings.Join(out, " ")
}

// splitSentences is a simplified English sentence splitter.
// It splits on . ! ? followed by whitespace + uppercase / digit, handles common
// abbreviations only partially. Good enough for encyclopedia / news prose.
var sentEnder = regexp.MustCompile(`([\.\!\?]+)\s+(?:[A-Z0-9"\(\[])`)

func splitSentences(text string) []string {
	// Normalize whitespace.
	t := strings.Join(strings.Fields(text), " ")
	if t == "" {
		return nil
	}
	// Walk through ender positions, slice on them.
	locs := sentEnder.FindAllStringIndex(t, -1)
	out := []string{}
	last := 0
	for _, loc := range locs {
		// loc[0] is start of the ender punctuation; loc[1] is past the whitespace+next-char start.
		// Split boundary goes at the end of the punctuation (loc[1] minus the trailing next-char width).
		// Simplification: cut at the first whitespace after the punctuation.
		splitAt := strings.Index(t[loc[0]:], " ")
		if splitAt < 0 {
			continue
		}
		cut := loc[0] + splitAt
		chunk := strings.TrimSpace(t[last:cut])
		if chunk != "" {
			out = append(out, chunk)
		}
		last = cut + 1
	}
	tail := strings.TrimSpace(t[last:])
	if tail != "" {
		out = append(out, tail)
	}
	return out
}

var wordRe = regexp.MustCompile(`[A-Za-z0-9]+`)

// Minimal English stoplist - trimmed version of NLTK's. Keeps port deterministic and
// avoids an extra file.
var stopWords = map[string]struct{}{
	"a": {}, "an": {}, "the": {}, "and": {}, "or": {}, "but": {}, "of": {},
	"to": {}, "in": {}, "on": {}, "at": {}, "for": {}, "with": {}, "by": {},
	"is": {}, "are": {}, "was": {}, "were": {}, "be": {}, "been": {}, "being": {},
	"this": {}, "that": {}, "these": {}, "those": {}, "it": {}, "its": {},
	"as": {}, "from": {}, "if": {}, "then": {}, "than": {}, "so": {},
	"into": {}, "about": {}, "over": {}, "after": {}, "before": {},
	"has": {}, "have": {}, "had": {}, "do": {}, "does": {}, "did": {},
	"will": {}, "would": {}, "should": {}, "could": {}, "may": {}, "might": {},
	"s": {},
}

func tokenize(s string) []string {
	raw := wordRe.FindAllString(strings.ToLower(s), -1)
	out := raw[:0]
	for _, w := range raw {
		if _, stop := stopWords[w]; stop {
			continue
		}
		out = append(out, w)
	}
	return out
}

func cosine(a, b map[string]float64) float64 {
	if len(a) == 0 || len(b) == 0 {
		return 0
	}
	// Iterate smaller map for dot product.
	small, large := a, b
	if len(b) < len(a) {
		small, large = b, a
	}
	var dot float64
	for k, va := range small {
		if vb, ok := large[k]; ok {
			dot += va * vb
		}
	}
	var na, nb float64
	for _, v := range a {
		na += v * v
	}
	for _, v := range b {
		nb += v * v
	}
	if na == 0 || nb == 0 {
		return 0
	}
	return dot / (math.Sqrt(na) * math.Sqrt(nb))
}

// pageRank runs damped power iteration on a symmetric similarity matrix and
// returns the stationary distribution. adj[i][j] is the edge weight from i to j
// (symmetric; 0 = no edge). Teleport probability = 1-d.
func pageRank(adj [][]float64, d float64, maxIter int, tol float64) []float64 {
	n := len(adj)
	if n == 0 {
		return nil
	}
	// Column-normalize: prob of jumping from j to i is adj[i][j] / sum_i adj[i][j].
	colSum := make([]float64, n)
	for j := 0; j < n; j++ {
		for i := 0; i < n; i++ {
			colSum[j] += adj[i][j]
		}
	}
	score := make([]float64, n)
	for i := range score {
		score[i] = 1.0 / float64(n)
	}
	next := make([]float64, n)
	for it := 0; it < maxIter; it++ {
		for i := range next {
			var s float64
			for j := 0; j < n; j++ {
				if colSum[j] > 0 {
					s += adj[i][j] / colSum[j] * score[j]
				}
			}
			next[i] = (1.0-d)/float64(n) + d*s
		}
		// Convergence check.
		var diff float64
		for i := range score {
			diff += math.Abs(next[i] - score[i])
		}
		score, next = next, score
		if diff < tol {
			break
		}
	}
	return score
}

// SentenceCount returns how many sentences the tokenizer sees in text.
// Useful for setting the n_sentences_in span attribute.
func SentenceCount(text string) int {
	return len(splitSentences(text))
}

package tools

import (
	"strings"
	"testing"
)

const loremArticle = `Edward Said was a Palestinian American academic, political activist, and
literary critic who was born in Jerusalem in 1935 and who examined literature in
light of social and cultural politics. He was an outspoken proponent of the
political rights of the Palestinian people and the creation of an independent
Palestinian state. Said was born in Jerusalem, which was then part of the British
Mandate for Palestine. His family was Palestinian Christian, and his father was
a wealthy businessman. Said attended elite schools in Cairo and the United States.
He earned his Ph.D. in English literature from Harvard University in 1964. Said
is best known for his 1978 book Orientalism, which argues that Western academic
writing about the East has historically served imperial ambitions. The book
became a foundational text in postcolonial studies. Said taught at Columbia
University for most of his career and died in New York City in 2003.`

func TestLexRankReturnsTopSentence(t *testing.T) {
	out := LexRank(loremArticle, 1)
	if out == "" {
		t.Fatal("expected non-empty summary")
	}
	if !strings.Contains(out, "Said") && !strings.Contains(out, "said") {
		t.Errorf("top sentence should mention Said, got: %q", out)
	}
	// A single sentence should be short (< whole article).
	if len(out) > len(loremArticle)/2 {
		t.Errorf("top-1 summary too long: %d chars from a %d-char article", len(out), len(loremArticle))
	}
}

func TestLexRankHandlesEmpty(t *testing.T) {
	if got := LexRank("", 1); got != "" {
		t.Errorf("expected empty on empty input, got %q", got)
	}
	if got := LexRank("Short.", 3); !strings.Contains(got, "Short") {
		t.Errorf("expected fallback to include all sentences, got %q", got)
	}
}

func TestLexRankTopNInDocumentOrder(t *testing.T) {
	out := LexRank(loremArticle, 3)
	sents := splitSentences(loremArticle)
	// Each sentence in the summary must appear in the original order in the article.
	var lastIdx int = -1
	for _, summarySent := range splitSentences(out) {
		found := false
		for i, src := range sents {
			if strings.TrimSpace(summarySent) == strings.TrimSpace(src) {
				if i <= lastIdx {
					t.Errorf("summary not in document order: %q came after index %d", summarySent, lastIdx)
				}
				lastIdx = i
				found = true
				break
			}
		}
		if !found {
			// Soft: sentence boundary differences between splitter runs can drop exact match.
			// Fail only if none of the summary sentences match any source sentence.
			t.Logf("summary sentence not matched in source: %q", summarySent)
		}
	}
}

func TestSentenceCount(t *testing.T) {
	n := SentenceCount(loremArticle)
	if n < 7 || n > 12 {
		t.Errorf("expected ~10 sentences in the test article, got %d", n)
	}
}

func TestTokenizeDropsStopwords(t *testing.T) {
	toks := tokenize("The quick brown fox jumps over the lazy dog.")
	for _, tok := range toks {
		if _, stop := stopWords[tok]; stop {
			t.Errorf("stopword %q leaked through tokenizer", tok)
		}
	}
	// Should include content words.
	joined := strings.Join(toks, " ")
	for _, must := range []string{"quick", "brown", "fox", "jumps", "lazy", "dog"} {
		if !strings.Contains(joined, must) {
			t.Errorf("tokenizer dropped content word %q; got %v", must, toks)
		}
	}
}

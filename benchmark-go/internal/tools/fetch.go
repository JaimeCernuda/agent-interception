package tools

import (
	"context"
	"io"
	"net"
	"net/http"
	"regexp"
	"strings"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

// ipv4Transport: same reason as agent.go - force tcp4 for machines with broken IPv6.
var ipv4Transport = &http.Transport{
	DialContext: func(ctx context.Context, network, addr string) (net.Conn, error) {
		d := &net.Dialer{Timeout: 30 * time.Second, KeepAlive: 30 * time.Second}
		switch network {
		case "tcp", "tcp6":
			network = "tcp4"
		}
		return d.DialContext(ctx, network, addr)
	},
	ForceAttemptHTTP2:     true,
	MaxIdleConns:          10,
	IdleConnTimeout:       90 * time.Second,
	TLSHandshakeTimeout:   10 * time.Second,
	ExpectContinueTimeout: 1 * time.Second,
}

const userAgent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " +
	"(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

const (
	fetchTimeout    = 10 * time.Second
	fetchMaxRetries = 2
)

// Fetch downloads a URL and returns its text content, emitting one tool.fetch span.
// Returns empty string on permanent failure; the caller can filter those out
// (matching Python's fetch_url behavior).
func Fetch(url string, o *obs.Observer) string {
	s := o.Start("tool.fetch", map[string]any{
		"tool.name":        "fetch_url",
		"tool.input_hash":  inputHash(url),
		"tool.url":         url,
		"tool.retry_count": 0,
	})
	defer s.End()

	text, retries, status := fetchWithRetries(url)
	s.Set("tool.retry_count", retries)
	s.Set("tool.http_status", status)
	s.Set("tool.output_size_bytes", len(text))
	return text
}

func fetchWithRetries(url string) (text string, retries int, lastStatus int) {
	client := &http.Client{Timeout: fetchTimeout, Transport: ipv4Transport}
	for attempt := 0; attempt <= fetchMaxRetries; attempt++ {
		body, status, err := fetchOnce(client, url)
		lastStatus = status
		if err == nil && status >= 200 && status < 300 {
			return extractText(body), retries, status
		}
		retries++
		if attempt == fetchMaxRetries {
			return "", retries, lastStatus
		}
		time.Sleep(time.Duration(500*(attempt+1)) * time.Millisecond)
	}
	return "", retries, lastStatus
}

func fetchOnce(c *http.Client, url string) (string, int, error) {
	ctx, cancel := context.WithTimeout(context.Background(), fetchTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return "", 0, err
	}
	req.Header.Set("User-Agent", userAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8")
	req.Header.Set("Accept-Language", "en-US,en;q=0.5")
	req.Header.Set("Accept-Encoding", "identity") // no compression so body is ready-to-parse
	req.Header.Set("Upgrade-Insecure-Requests", "1")
	resp, err := c.Do(req)
	if err != nil {
		return "", 0, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 8<<20)) // cap at 8 MiB
	if err != nil {
		return "", resp.StatusCode, err
	}
	return string(body), resp.StatusCode, nil
}

// extractText strips scripts/styles and HTML tags to get readable plain text.
// This is intentionally simpler than Python's trafilatura. For the benchmark
// we document this asymmetry and compare summarize-input byte counts instead
// of text equality.
var (
	scriptRe  = regexp.MustCompile(`(?is)<script[^>]*>.*?</script>`)
	styleRe   = regexp.MustCompile(`(?is)<style[^>]*>.*?</style>`)
	commentRe = regexp.MustCompile(`(?s)<!--.*?-->`)
	tagRe     = regexp.MustCompile(`<[^>]+>`)
	wsRe      = regexp.MustCompile(`[ \t]+`)
	blanksRe  = regexp.MustCompile(`\n{3,}`)
)

func extractText(html string) string {
	t := scriptRe.ReplaceAllString(html, " ")
	t = styleRe.ReplaceAllString(t, " ")
	t = commentRe.ReplaceAllString(t, " ")
	t = tagRe.ReplaceAllString(t, " ")
	t = decodeEntities(t)
	// Normalize whitespace line by line.
	lines := strings.Split(t, "\n")
	out := make([]string, 0, len(lines))
	for _, ln := range lines {
		ln = wsRe.ReplaceAllString(ln, " ")
		ln = strings.TrimSpace(ln)
		if ln != "" {
			out = append(out, ln)
		}
	}
	joined := strings.Join(out, "\n")
	return blanksRe.ReplaceAllString(joined, "\n\n")
}

var entityMap = map[string]string{
	"&nbsp;":  " ",
	"&amp;":   "&",
	"&lt;":    "<",
	"&gt;":    ">",
	"&quot;":  `"`,
	"&apos;":  "'",
	"&#39;":   "'",
	"&rsquo;": "'",
	"&lsquo;": "'",
	"&rdquo;": `"`,
	"&ldquo;": `"`,
	"&ndash;": "–",
	"&mdash;": "—",
	"&hellip;": "…",
}

func decodeEntities(s string) string {
	for k, v := range entityMap {
		s = strings.ReplaceAll(s, k, v)
	}
	return s
}

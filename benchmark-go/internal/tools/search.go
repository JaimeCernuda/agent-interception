package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"regexp"
	"strings"
	"sync"
	"time"

	"github.com/annamonso/agent-interception/benchmark-go/internal/obs"
)

const searchTimeout = 10 * time.Second

// Static URL map, populated at program start by run.go reading the queries JSON.
var (
	staticMu   sync.RWMutex
	staticURLs = map[string][]string{}
)

// RegisterStaticURLs is the Go counterpart of Python's
// benchmark.tools.search.register_static_urls. Called once from main.
func RegisterStaticURLs(m map[string][]string) {
	staticMu.Lock()
	defer staticMu.Unlock()
	staticURLs = make(map[string][]string, len(m))
	for k, v := range m {
		staticURLs[k] = append([]string(nil), v...)
	}
}

// Search returns up to topK URLs for the query, emitting one tool.search span.
// queryID is required if SEARCH_BACKEND=static.
func Search(query string, queryID string, topK int, o *obs.Observer) []string {
	backend := resolveBackend()
	s := o.Start("tool.search", map[string]any{
		"tool.name":        backend,
		"tool.input_hash":  inputHash(query),
		"tool.retry_count": 0,
	})
	defer s.End()

	urls, retries, err := dispatch(backend, query, queryID, topK)
	if err != nil {
		s.Fail(err)
	}
	s.Set("tool.retry_count", retries)
	s.Set("tool.num_results", len(urls))
	var size int
	for _, u := range urls {
		size += len(u)
	}
	s.Set("tool.output_size_bytes", size)
	return urls
}

func resolveBackend() string {
	v := strings.ToLower(os.Getenv("SEARCH_BACKEND"))
	switch v {
	case "google_cse", "ddg", "static":
		return v
	}
	// auto
	if os.Getenv("GOOGLE_API_KEY") != "" && os.Getenv("GOOGLE_CX") != "" {
		return "google_cse"
	}
	return "static"
}

func dispatch(backend, query, queryID string, topK int) ([]string, int, error) {
	switch backend {
	case "google_cse":
		return googleCSE(query, topK)
	case "ddg":
		return ddg(query, topK)
	default:
		return staticLookup(queryID, topK)
	}
}

func staticLookup(queryID string, topK int) ([]string, int, error) {
	if queryID == "" {
		return nil, 0, fmt.Errorf("SEARCH_BACKEND=static requires a query_id")
	}
	staticMu.RLock()
	defer staticMu.RUnlock()
	urls, ok := staticURLs[queryID]
	if !ok || len(urls) == 0 {
		return nil, 0, fmt.Errorf("no static urls registered for query_id=%s", queryID)
	}
	if len(urls) > topK {
		urls = urls[:topK]
	}
	return append([]string(nil), urls...), 0, nil
}

func googleCSE(query string, topK int) ([]string, int, error) {
	endpoint := "https://www.googleapis.com/customsearch/v1"
	num := topK
	if num > 10 {
		num = 10
	}
	params := url.Values{}
	params.Set("key", os.Getenv("GOOGLE_API_KEY"))
	params.Set("cx", os.Getenv("GOOGLE_CX"))
	params.Set("q", query)
	params.Set("num", fmt.Sprintf("%d", num))

	var retries int
	var lastErr error
	client := &http.Client{Timeout: searchTimeout}
	for attempt := 0; attempt < 3; attempt++ {
		u := endpoint + "?" + params.Encode()
		ctx, cancel := context.WithTimeout(context.Background(), searchTimeout)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, u, nil)
		req.Header.Set("User-Agent", userAgent)
		resp, err := client.Do(req)
		cancel()
		if err != nil {
			retries++
			lastErr = err
			time.Sleep(time.Duration(500*(attempt+1)) * time.Millisecond)
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 {
			retries++
			lastErr = fmt.Errorf("google_cse status %d", resp.StatusCode)
			time.Sleep(time.Duration(500*(attempt+1)) * time.Millisecond)
			continue
		}
		var parsed struct {
			Items []struct {
				Link string `json:"link"`
			} `json:"items"`
		}
		if err := json.Unmarshal(body, &parsed); err != nil {
			retries++
			lastErr = err
			continue
		}
		urls := make([]string, 0, len(parsed.Items))
		for _, it := range parsed.Items {
			if it.Link != "" {
				urls = append(urls, it.Link)
			}
		}
		if len(urls) > topK {
			urls = urls[:topK]
		}
		return urls, retries, nil
	}
	return nil, retries, lastErr
}

// DDG HTML scrape - the same endpoint the Python ddgs package uses.
// Ugly but zero-dep and honest.
var ddgResultRe = regexp.MustCompile(`<a[^>]+class="result__a"[^>]+href="([^"]+)"`)

func ddg(query string, topK int) ([]string, int, error) {
	endpoint := "https://html.duckduckgo.com/html/"
	form := url.Values{}
	form.Set("q", query)

	client := &http.Client{Timeout: searchTimeout}
	var retries int
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), searchTimeout)
		req, _ := http.NewRequestWithContext(ctx, http.MethodPost, endpoint,
			strings.NewReader(form.Encode()))
		req.Header.Set("User-Agent", userAgent)
		req.Header.Set("Content-Type", "application/x-www-form-urlencoded")
		resp, err := client.Do(req)
		cancel()
		if err != nil {
			retries++
			lastErr = err
			time.Sleep(time.Duration(1000*(attempt+1)) * time.Millisecond)
			continue
		}
		body, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		if resp.StatusCode != 200 {
			retries++
			lastErr = fmt.Errorf("ddg status %d", resp.StatusCode)
			continue
		}
		matches := ddgResultRe.FindAllStringSubmatch(string(body), topK)
		urls := make([]string, 0, len(matches))
		for _, m := range matches {
			if len(m) >= 2 {
				// DDG wraps URLs in a redirect; strip the u= param if present.
				link := m[1]
				if strings.Contains(link, "uddg=") {
					if i := strings.Index(link, "uddg="); i >= 0 {
						raw := link[i+len("uddg="):]
						if amp := strings.Index(raw, "&"); amp >= 0 {
							raw = raw[:amp]
						}
						if decoded, err := url.QueryUnescape(raw); err == nil {
							link = decoded
						}
					}
				}
				urls = append(urls, link)
			}
		}
		if len(urls) > 0 {
			return urls, retries, nil
		}
		retries++
		lastErr = fmt.Errorf("ddg returned no results")
	}
	return nil, retries, lastErr
}

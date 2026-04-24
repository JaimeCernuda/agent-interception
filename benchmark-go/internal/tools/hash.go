package tools

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

// inputHash mirrors benchmark.obs.input_hash: sha256 of the canonical
// string form of x, first 16 hex chars. Used for tool.input_hash attrs.
func inputHash(x any) string {
	var data []byte
	switch v := x.(type) {
	case []byte:
		data = v
	case string:
		data = []byte(v)
	default:
		b, err := json.Marshal(v)
		if err != nil {
			data = []byte(fmt.Sprintf("%v", v))
		} else {
			data = b
		}
	}
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])[:16]
}

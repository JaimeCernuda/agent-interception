package chemcrow

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
)

// inputHash matches benchmark.obs.input_hash and internal/tools.inputHash:
// sha256(canonical(x))[:16] hex. Duplicated here to keep this package self-
// contained; if a third copy ever appears, factor it into internal/obs.
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

package safe

import "testing"

func TestSafeHeaderIndexSupportsAliases(t *testing.T) {
	headers := map[string]int{
		"日期": 0,
		"美元": 1,
		"日元": 3,
		"港元": 4,
	}

	index, ok := safeHeaderIndex(headers, []string{"港币", "港元"})
	if !ok {
		t.Fatalf("expected 港元 alias to resolve")
	}
	if index != 4 {
		t.Fatalf("index = %d, want 4", index)
	}

	index, ok = safeHeaderIndex(headers, []string{"日元"})
	if !ok {
		t.Fatalf("expected 日元 header to resolve")
	}
	if index != 3 {
		t.Fatalf("index = %d, want 3", index)
	}
}

package snapshot

import "strings"

func publicSourceLabel(source string) string {
	return strings.TrimSpace(source)
}

func publicVisibleText(value string) string {
	return strings.TrimSpace(value)
}

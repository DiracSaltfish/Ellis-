package iopv

import (
	"os"
	"testing"
)

func TestGFFModelMetadata(t *testing.T) {
	raw, e := os.ReadFile("testdata/pcf/520600_20260911.html")
	if e != nil {
		t.Fatal(e)
	}
	b, e := ParsePCF(raw, "520600.SH", "2026-09-11")
	if e != nil {
		t.Fatal(e)
	}
	if b.PrevDate != "2026-09-10" || b.PrevNAV == nil || *b.PrevNAV != .9629 || b.CreationAllowed == nil || !*b.CreationAllowed || b.RedemptionAllowed == nil || !*b.RedemptionAllowed || b.CreationLimit == nil || *b.CreationLimit != 200000000 {
		t.Fatalf("%+v", b)
	}
}
func TestXMLModelMetadata(t *testing.T) {
	raw, e := os.ReadFile("testdata/pcf/520570.xml")
	if e != nil {
		t.Fatal(e)
	}
	b, e := ParsePCF(raw, "520570.SH", "2026-09-08")
	if e != nil {
		t.Fatal(e)
	}
	if b.PrevDate != "2026-09-07" || b.PrevNAV == nil || b.CreationAllowed == nil || !*b.CreationAllowed || b.CreationLimit == nil || *b.CreationLimit != 1000000000 {
		t.Fatalf("%+v", b)
	}
}

package snapshot

import (
	"strings"
	"testing"
	"time"

	"newnavnav/internal/domain"
)

func TestRenderBranchHTMLOmitsReferenceSectionAndESTHeading(t *testing.T) {
	html := RenderBranchHTML(domain.BranchSnapshot{
		AsOf: time.Date(2026, 6, 16, 15, 0, 0, 0, time.UTC),
		Branch: domain.Branch{
			Key:    "qdiijpn",
			NameCN: "日本QDII",
		},
		EstimateRows: []domain.EstimateRow{
			{
				Symbol:          "SZ159866",
				Name:            "日经ETF",
				OfficialEst:     1.2345,
				FairEst:         1.2501,
				OfficialPremium: 0.32,
				FairPremium:     1.58,
				ModelVersion:    "holdings",
			},
		},
	})

	if strings.Contains(html, "参考数据") {
		t.Fatalf("html should omit reference section: %s", html)
	}
	if strings.Contains(html, "<h2>EST</h2>") {
		t.Fatalf("html should omit EST heading: %s", html)
	}
	if !strings.Contains(html, "T-2官方公布净值") {
		t.Fatalf("html missing estimate table header: %s", html)
	}
}

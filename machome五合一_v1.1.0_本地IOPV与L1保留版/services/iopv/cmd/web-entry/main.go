// web-entry provides the port-80 website without changing the hub's internal URL.
package main

import (
	"flag"
	"intranet-iopv/internal/live"
	"os"
	"path/filepath"

	"intranet-iopv/web"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
	"time"
)

func main() {
	listen := flag.String("listen", ":80", "website address")
	upstream := flag.String("upstream", "http://127.0.0.1:18680", "IOPV service")
	home, _ := os.UserHomeDir()
	rankingDB := flag.String("ranking-db", filepath.Join(home, "Library/Application Support/MachomeHub/data/premium/local-iopv/data/iopv.sqlite"), "read-only ranking archive")
	flag.Parse()
	target, err := url.Parse(*upstream)
	if err != nil {
		log.Fatal(err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.FlushInterval = -1
	pages := web.Handler()
	review := live.RankingReviewHandler(*target, *rankingDB)
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Preserve the original service's local-only management write boundary.
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		ip := net.ParseIP(host)
		if strings.HasPrefix(r.URL.Path, "/api/v1/manage") && (ip == nil || !ip.IsLoopback()) {
			http.Error(w, "Management writes require local access", http.StatusForbidden)
			return
		}
		if r.URL.Path == "/api/v1/ranking" && r.URL.Query().Get("review") == "1" {
			review.ServeHTTP(w, r)
			return
		}
		// Serve UI assets independently of the running valuation collector.
		if r.URL.Path == "/hk-connect-redemption" || r.URL.Path == "/hk-redemption.js" || r.URL.Path == "/" || r.URL.Path == "/manage" || r.URL.Path == "/app.js" || r.URL.Path == "/netting.js" || r.URL.Path == "/netting-ui.js" || r.URL.Path == "/chart-range.js" || r.URL.Path == "/style.css" || r.URL.Path == "/ranking" || r.URL.Path == "/ranking.js" {
			w.Header().Set("Cache-Control", "no-cache")
			pages.ServeHTTP(w, r)
			return
		}
		proxy.ServeHTTP(w, r)
	})
	log.Fatal((&http.Server{Addr: *listen, Handler: handler, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}).ListenAndServe())
}

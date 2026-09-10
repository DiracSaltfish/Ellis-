// web-entry provides the port-80 website without changing the hub's internal URL.
package main

import (
	"flag"
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
	flag.Parse()
	target, err := url.Parse(*upstream)
	if err != nil {
		log.Fatal(err)
	}
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.FlushInterval = -1
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Preserve the original service's local-only management write boundary.
		host, _, _ := net.SplitHostPort(r.RemoteAddr)
		ip := net.ParseIP(host)
		if strings.HasPrefix(r.URL.Path, "/api/v1/manage") && (ip == nil || !ip.IsLoopback()) {
			http.Error(w, "Management writes require local access", http.StatusForbidden)
			return
		}
		proxy.ServeHTTP(w, r)
	})
	log.Fatal((&http.Server{Addr: *listen, Handler: handler, ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}).ListenAndServe())
}

package web

import (
	"embed"
	"net/http"
)

//go:embed index.html netting.js netting-ui.js chart-range.js app.js style.css ledger.json ranking.html ranking.js hk-redemption.html hk-redemption.js
var files embed.FS

func Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/hk-connect-redemption" {
			http.ServeFileFS(w, r, files, "hk-redemption.html")
			return
		}
		if r.URL.Path == "/ranking" {
			http.ServeFileFS(w, r, files, "ranking.html")
			return
		}
		if r.URL.Path == "/manage" {
			http.ServeFileFS(w, r, files, "index.html")
			return
		}
		http.FileServerFS(files).ServeHTTP(w, r)
	})
}

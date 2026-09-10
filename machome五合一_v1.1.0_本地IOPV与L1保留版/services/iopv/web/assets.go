package web

import (
	"embed"
	"net/http"
)

//go:embed index.html app.js style.css ledger.json
var files embed.FS

func Handler() http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/manage" {
			http.ServeFileFS(w, r, files, "index.html")
			return
		}
		http.FileServerFS(files).ServeHTTP(w, r)
	})
}

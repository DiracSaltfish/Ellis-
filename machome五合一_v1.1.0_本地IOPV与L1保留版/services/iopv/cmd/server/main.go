package main

import (
	"context"
	"encoding/json"
	"flag"
	"intranet-iopv/internal/live"
	"intranet-iopv/web"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	path := flag.String("config", "config.local.json", "config file")
	flag.Parse()
	raw, e := os.ReadFile(*path)
	if e != nil {
		log.Fatal(e)
	}
	var cfg live.Config
	if e = json.Unmarshal(raw, &cfg); e != nil {
		log.Fatal(e)
	}
	listener, e := net.Listen("tcp", cfg.Listen)
	if e != nil {
		log.Fatal(e)
	}
	s, e := live.New(cfg)
	if e != nil {
		log.Fatal(e)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if cfg.ParentPID > 0 {
		go func() {
			t := time.NewTicker(time.Second)
			defer t.Stop()
			for {
				select {
				case <-ctx.Done():
					return
				case <-t.C:
					if os.Getppid() != cfg.ParentPID {
						cancel()
						return
					}
				}
			}
		}()
	}
	done := make(chan struct{})
	go func() { s.Run(ctx); close(done) }()
	server := &http.Server{Addr: cfg.Listen, Handler: s.Handler(web.Handler()), ReadHeaderTimeout: 5 * time.Second, IdleTimeout: 60 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, c := context.WithTimeout(context.Background(), 5*time.Second)
		defer c()
		server.Shutdown(shutdown)
	}()
	log.Printf("IOPV web http://%s / management /manage", cfg.Listen)
	if e = server.Serve(listener); e != nil && e != http.ErrServerClosed {
		log.Fatal(e)
	}
	<-done
}

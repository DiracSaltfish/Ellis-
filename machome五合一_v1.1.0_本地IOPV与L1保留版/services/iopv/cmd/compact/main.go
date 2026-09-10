package main

import (
	"flag"
	"intranet-iopv/internal/live"
	"log"
)

func main() {
	p := flag.String("db", "", "OFFLINE backed-up database path")
	flag.Parse()
	if *p == "" {
		log.Fatal("-db required")
	}
	s, e := live.OpenStore(*p)
	if e != nil {
		log.Fatal(e)
	}
	defer s.DB.Close()
	n, e := s.CompactLegacy()
	if e != nil {
		log.Fatal(e)
	}
	if e = s.Check(); e != nil {
		log.Fatal(e)
	}
	log.Printf("compacted %d legacy points; integrity OK", n)
}

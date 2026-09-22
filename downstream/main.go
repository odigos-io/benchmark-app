// Echo service: the downstream hop of the bucket benchmark.
// POST/GET /echo?delay_ms=N sleeps N ms (clamped to 0..5000) and echoes the request body.
package main

import (
	"io"
	"log"
	"net/http"
	"os"
	"strconv"
	"time"
)

const maxDelayMs = 5000

func echo(w http.ResponseWriter, r *http.Request) {
	body, err := io.ReadAll(r.Body)
	if err != nil {
		http.Error(w, "read body: "+err.Error(), http.StatusBadRequest)
		return
	}
	delay, _ := strconv.Atoi(r.URL.Query().Get("delay_ms"))
	if delay < 0 {
		delay = 0
	} else if delay > maxDelayMs {
		delay = maxDelayMs
	}
	if delay > 0 {
		time.Sleep(time.Duration(delay) * time.Millisecond)
	}
	if ct := r.Header.Get("Content-Type"); ct != "" {
		w.Header().Set("Content-Type", ct)
	}
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.WriteHeader(http.StatusOK)
	w.Write(body)
}

func healthz(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "text/plain")
	io.WriteString(w, "ok\n")
}

func main() {
	addr := os.Getenv("ADDR")
	if addr == "" {
		addr = ":8080"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/echo", echo)
	mux.HandleFunc("/healthz", healthz)
	srv := &http.Server{
		Addr:              addr,
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	log.Printf("echo listening on %s", addr)
	log.Fatal(srv.ListenAndServe())
}

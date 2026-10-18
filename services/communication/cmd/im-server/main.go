package main

import (
	"context"
	"flag"
	"log/slog"
	"net/http"
	"octop/communication/internal/config"
	"octop/communication/internal/deps"
	"octop/communication/internal/httpx"
	"octop/communication/internal/idgen"
	"octop/communication/internal/serverapp"
	"octop/communication/internal/store"
	"os"
	"os/signal"
	"syscall"
	"time"
)

func main() {
	migrate := flag.Bool("migrate", false, "apply communication migrations and exit")
	flag.Parse()
	log := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	cfg, e := config.Load()
	if e != nil {
		log.Error("configuration failed", "error", e)
		os.Exit(2)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	st, e := store.Open(ctx, cfg.DatabaseURL, cfg.Resource.PoolMaxConns)
	if e != nil {
		log.Error("database unavailable", "error", e)
		os.Exit(2)
	}
	defer st.Close()
	if *migrate {
		if _, e = st.Migrate(ctx); e != nil {
			log.Error("migration failed", "error", e)
			os.Exit(2)
		}
		return
	}
	if e = st.CheckShape(ctx); e != nil {
		log.Error("schema mismatch", "error", e)
		os.Exit(2)
	}
	d := &deps.Deps{Cfg: cfg, Log: log, Store: st, Cursor: httpx.NewCursor(cfg.CursorKey), InstanceID: idgen.New("im")}
	a := serverapp.New(d)
	if e = a.Start(ctx); e != nil {
		log.Error("workers unavailable", "error", e)
		os.Exit(2)
	}
	defer a.Stop()
	srv := &http.Server{Addr: cfg.Listen, Handler: a.Handler(st), ReadHeaderTimeout: 10 * time.Second, ReadTimeout: 120 * time.Second, IdleTimeout: 120 * time.Second}
	go func() {
		<-ctx.Done()
		c, stop := context.WithTimeout(context.Background(), 15*time.Second)
		defer stop()
		_ = srv.Shutdown(c)
	}()
	if e = srv.ListenAndServe(); e != nil && e != http.ErrServerClosed {
		log.Error("server failed", "error", e)
		os.Exit(2)
	}
}

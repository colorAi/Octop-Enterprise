package serverapp

import (
	"context"
	"encoding/json"
	"net/http"
	"octop/communication/internal/auth"
	"octop/communication/internal/chat"
	"octop/communication/internal/deps"
	"octop/communication/internal/groups"
	"octop/communication/internal/httpx"
	"octop/communication/internal/jobs"
	"octop/communication/internal/mediasvc"
	"octop/communication/internal/organization"
	"octop/communication/internal/realtime"
	"octop/communication/internal/store"
	"octop/communication/internal/syncsrv"
	"time"
)

type App struct {
	Mux *http.ServeMux
	Sup *jobs.Supervisor
	Hub *realtime.Hub
}

func New(d *deps.Deps) *App {
	a := &App{http.NewServeMux(), jobs.New(d), realtime.NewHub(d.Log, d.Cfg.Resource.WSSMaxConns, d.Cfg.Resource.WSSMaxPerUser)}
	d.Realtime = a.Hub
	a.Sup.Register("media.process", mediasvc.NewProcessJob(d))
	a.Sup.RegisterRecurring("media.cleanup", time.Hour, mediasvc.NewCleanupJob(d))
	a.Sup.RegisterRecurring("sync.purge", time.Hour, syncsrv.NewPurgeJob(d))
	a.Sup.Register("sync_wakeup", func(ctx context.Context, b json.RawMessage) error {
		var v struct {
			UserID string `json:"user_id"`
		}
		if json.Unmarshal(b, &v) != nil || v.UserID == "" {
			return jobs.ErrPermanent
		}
		a.Hub.Wakeup(v.UserID)
		return nil
	})
	chat.New(d).Register(a.Mux)
	groups.New(d).Register(a.Mux)
	syncsrv.New(d).Register(a.Mux)
	mediasvc.New(d).Register(a.Mux)
	organization.New(d).Register(a.Mux)
	a.Mux.HandleFunc("GET /api/app/v1/realtime", auth.Middleware(d, func(w http.ResponseWriter, r *http.Request) { u, _ := auth.From(r); a.Hub.Attach(w, r, u.UserID) }))
	a.Mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, r *http.Request) {
		if !d.Store.Healthy(r.Context()) {
			w.WriteHeader(503)
			return
		}
		w.WriteHeader(200)
	})
	a.Mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) { httpx.WriteError(w, r, httpx.ErrNotFound) })
	return a
}
func (a *App) Start(ctx context.Context) error {
	return a.Sup.Start(ctx)
}
func (a *App) Stop() { a.Hub.Close(); a.Sup.Stop(context.Background()) }
func (a *App) Handler(st *store.Store) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != "GET" && r.Method != "HEAD" {
			done, e := st.BeginWrite(r.Context())
			if e != nil {
				httpx.WriteError(w, r, httpx.ErrMaintenance)
				return
			}
			defer done()
		}
		r = r.WithContext(context.WithValue(r.Context(), httpx.RequestIDKey, httpx.RequestID(r.Context())))
		a.Mux.ServeHTTP(w, r)
	})
}

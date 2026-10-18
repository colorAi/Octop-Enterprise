// Package migrations embeds versioned SQL files applied in numeric order.
package migrations

import "embed"

//go:embed *.sql
var FS embed.FS

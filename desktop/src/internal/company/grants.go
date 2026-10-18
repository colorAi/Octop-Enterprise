package company

import (
	"context"
	"errors"
	"github.com/coder/websocket/wsjson"
	"time"
)

func isFileTool(tool string) bool {
	switch tool {
	case "local_list_directory", "local_read_file", "local_write_file", "local_operation_status":
		return true
	}
	return false
}
func (c *Client) Grants() []Grant { return c.files.Grants() }
func (c *Client) AddGrant(ctx context.Context, path string, writable, allowCompany bool) (Grant, error) {
	c.mu.Lock()
	if c.status.State != "online" {
		c.mu.Unlock()
		return Grant{}, errors.New("请先连接设备 / Connect device first")
	}
	g, err := c.files.Add(path, writable, allowCompany)
	c.mu.Unlock()
	if err != nil {
		return g, err
	}
	if err = c.publishGrants(ctx); err != nil {
		c.files.Revoke(g.ID)
		return Grant{}, err
	}
	return g, nil
}
func (c *Client) RevokeGrant(ctx context.Context, id string) error {
	c.files.Revoke(id)
	return c.publishGrants(ctx)
}
func (c *Client) publishGrants(ctx context.Context) error {
	c.mu.Lock()
	conn := c.conn
	c.mu.Unlock()
	if conn == nil {
		return nil
	}
	ctx, done := context.WithTimeout(ctx, 5*time.Second)
	defer done()
	return wsjson.Write(ctx, conn, map[string]any{"type": "grants", "grants": wireGrants(c.files.Grants())})
}

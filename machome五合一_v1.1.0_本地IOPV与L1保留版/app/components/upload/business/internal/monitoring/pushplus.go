package monitoring

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const defaultPushPlusEndpoint = "https://www.pushplus.plus/send"

// Sender is deliberately small so incident throttling can be tested without
// sending external notifications.
type Sender interface {
	Send(ctx context.Context, title string, content string) error
}

type PushPlusClient struct {
	Token      string
	Endpoint   string
	Channel    string
	Template   string
	HTTPClient *http.Client
}

type pushPlusRequest struct {
	Token     string `json:"token"`
	Title     string `json:"title,omitempty"`
	Content   string `json:"content"`
	Template  string `json:"template,omitempty"`
	Channel   string `json:"channel,omitempty"`
	Timestamp int64  `json:"timestamp,omitempty"`
}

type pushPlusResponse struct {
	Code int             `json:"code"`
	Msg  string          `json:"msg"`
	Data json.RawMessage `json:"data"`
}

func (c *PushPlusClient) Send(ctx context.Context, title string, content string) error {
	if c == nil || strings.TrimSpace(c.Token) == "" {
		return fmt.Errorf("pushplus token is not configured")
	}
	endpoint := strings.TrimSpace(c.Endpoint)
	if endpoint == "" {
		endpoint = defaultPushPlusEndpoint
	}
	template := strings.TrimSpace(c.Template)
	if template == "" {
		template = "markdown"
	}
	channel := strings.TrimSpace(c.Channel)
	if channel == "" {
		channel = "wechat"
	}
	payload, err := json.Marshal(pushPlusRequest{
		Token:     strings.TrimSpace(c.Token),
		Title:     strings.TrimSpace(title),
		Content:   strings.TrimSpace(content),
		Template:  template,
		Channel:   channel,
		Timestamp: time.Now().Add(2 * time.Minute).UnixMilli(),
	})
	if err != nil {
		return fmt.Errorf("encode pushplus request: %w", err)
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(payload))
	if err != nil {
		return fmt.Errorf("create pushplus request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("User-Agent", "newnavnav-monitor/1.0")
	client := c.HTTPClient
	if client == nil {
		client = &http.Client{Timeout: 10 * time.Second}
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("pushplus request failed: %w", err)
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	if err != nil {
		return fmt.Errorf("read pushplus response: %w", err)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("pushplus returned HTTP %d", resp.StatusCode)
	}
	var result pushPlusResponse
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("pushplus returned invalid JSON")
	}
	if result.Code != http.StatusOK {
		message := strings.TrimSpace(result.Msg)
		if message == "" {
			message = "request rejected"
		}
		return fmt.Errorf("pushplus rejected request: code=%d message=%s", result.Code, message)
	}
	return nil
}

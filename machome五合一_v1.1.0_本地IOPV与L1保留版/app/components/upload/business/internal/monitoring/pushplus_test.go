package monitoring

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestPushPlusClientSendsMarkdownWithoutLeakingTokenIntoURL(t *testing.T) {
	var request pushPlusRequest
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/send" || r.URL.RawQuery != "" {
			t.Fatalf("request URL = %s", r.URL.String())
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
			t.Fatal(err)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"code": 200, "msg": "ok", "data": "short-code"})
	}))
	defer server.Close()

	client := &PushPlusClient{Token: "test-secret", Endpoint: server.URL + "/send", HTTPClient: server.Client()}
	if err := client.Send(context.Background(), "title", "content"); err != nil {
		t.Fatal(err)
	}
	if request.Token != "test-secret" || request.Template != "markdown" || request.Channel != "wechat" {
		t.Fatalf("request = %+v", request)
	}
	if request.Timestamp <= 0 {
		t.Fatal("timestamp was not set")
	}
}

func TestPushPlusClientRejectsNonSuccessCode(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{"code": 500, "msg": "denied"})
	}))
	defer server.Close()
	client := &PushPlusClient{Token: "test-secret", Endpoint: server.URL, HTTPClient: server.Client()}
	if err := client.Send(context.Background(), "title", "content"); err == nil {
		t.Fatal("expected rejected response")
	}
}

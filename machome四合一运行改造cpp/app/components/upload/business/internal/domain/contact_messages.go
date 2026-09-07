package domain

import "time"

type ContactMessage struct {
	ID        int64     `json:"id"`
	Email     string    `json:"email"`
	Message   string    `json:"message"`
	CreatedAt time.Time `json:"created_at"`
}

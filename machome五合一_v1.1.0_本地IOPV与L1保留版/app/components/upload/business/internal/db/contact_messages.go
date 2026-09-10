package db

import (
	"context"

	"newnavnav/internal/domain"
)

func (r *Repository) CreateContactMessage(ctx context.Context, email string, message string) error {
	_, err := r.db.ExecContext(ctx, `
INSERT INTO contact_messages (email, message_text)
VALUES (?, ?)`,
		email,
		message,
	)
	return err
}

func (r *Repository) LoadContactMessages(ctx context.Context, limit int) ([]domain.ContactMessage, error) {
	if limit <= 0 {
		limit = 100
	}
	if limit > 200 {
		limit = 200
	}
	rows, err := r.db.QueryContext(ctx, `
SELECT id, email, message_text, created_at
FROM contact_messages
ORDER BY id DESC
LIMIT ?`,
		limit,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := make([]domain.ContactMessage, 0, limit)
	for rows.Next() {
		var row domain.ContactMessage
		if err := rows.Scan(&row.ID, &row.Email, &row.Message, &row.CreatedAt); err != nil {
			return nil, err
		}
		out = append(out, row)
	}
	return out, rows.Err()
}

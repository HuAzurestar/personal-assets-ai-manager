CREATE TABLE IF NOT EXISTS position_leg /* 不可变Review发布的有据数量变化 */ (
 id INTEGER PRIMARY KEY, position_id INTEGER NOT NULL, review_id INTEGER NOT NULL,
 type TEXT NOT NULL, leg_amount INTEGER NOT NULL, leg_direction TEXT NOT NULL,
 occurred_time TEXT NOT NULL, source_position_leg_id INTEGER NOT NULL DEFAULT 0,
 basis TEXT NOT NULL DEFAULT '',
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
CREATE INDEX IF NOT EXISTS ix_position_leg_position_time ON position_leg(position_id,occurred_time,id);
CREATE INDEX IF NOT EXISTS ix_position_leg_review ON position_leg(review_id,id);

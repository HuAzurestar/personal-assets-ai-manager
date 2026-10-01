CREATE TABLE IF NOT EXISTS position /* 独立资产或负债对象身份 */ (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 type TEXT NOT NULL, usage_scenario TEXT NOT NULL, party_id INTEGER NOT NULL,
 counterparty TEXT NOT NULL DEFAULT '', unit_code TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'ACTIVE',
 created_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now')),
 updated_time TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%f000Z','now'))
);
CREATE INDEX IF NOT EXISTS ix_position_party_type_usage ON position(party_id,type,usage_scenario,status,id);

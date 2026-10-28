-- 0001_baseline: the schema as SQLAlchemy create_all made it at the start of the migration system (Oct 2026).
-- Existing databases (a `channels` table already present) are marked applied without running this.

CREATE TABLE IF NOT EXISTS api_tokens (
	id VARCHAR NOT NULL, 
	name VARCHAR NOT NULL, 
	token_hash VARCHAR NOT NULL, 
	prefix VARCHAR NOT NULL, 
	scopes JSON NOT NULL, 
	tools JSON NOT NULL, 
	require_approval BOOLEAN NOT NULL, 
	rate_per_min INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	last_used_at DATETIME, 
	PRIMARY KEY (id), 
	UNIQUE (token_hash)
);

CREATE TABLE IF NOT EXISTS audit_log (
	id INTEGER NOT NULL, 
	at DATETIME NOT NULL, 
	actor VARCHAR NOT NULL, 
	token_id VARCHAR, 
	tool VARCHAR NOT NULL, 
	method VARCHAR NOT NULL, 
	path VARCHAR NOT NULL, 
	status INTEGER NOT NULL, 
	detail TEXT NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_audit_log_at ON audit_log (at);

CREATE TABLE IF NOT EXISTS channels (
	id VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	external_id VARCHAR NOT NULL, 
	username VARCHAR NOT NULL, 
	display_name VARCHAR NOT NULL, 
	avatar_url TEXT NOT NULL, 
	access_token_enc TEXT, 
	refresh_token_enc TEXT, 
	token_expires_at DATETIME, 
	refresh_expires_at DATETIME, 
	token_obtained_at DATETIME, 
	scopes TEXT NOT NULL, 
	status VARCHAR NOT NULL, 
	status_detail TEXT NOT NULL, 
	timezone VARCHAR NOT NULL, 
	daily_limit INTEGER NOT NULL, 
	paused BOOLEAN NOT NULL, 
	meta JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_channel_provider_ext UNIQUE (provider, external_id)
);
CREATE INDEX IF NOT EXISTS ix_channels_provider ON channels (provider);

CREATE TABLE IF NOT EXISTS events (
	id INTEGER NOT NULL, 
	at DATETIME NOT NULL, 
	level VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	message TEXT NOT NULL, 
	target_id VARCHAR, 
	channel_id VARCHAR, 
	notify BOOLEAN NOT NULL, 
	read BOOLEAN NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_events_at ON events (at);
CREATE INDEX IF NOT EXISTS ix_events_target_id ON events (target_id);

CREATE TABLE IF NOT EXISTS ideas (
	id VARCHAR NOT NULL, 
	title VARCHAR NOT NULL, 
	body TEXT NOT NULL, 
	"column" VARCHAR NOT NULL, 
	tags JSON NOT NULL, 
	media_ids JSON NOT NULL, 
	position INTEGER NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);

CREATE TABLE IF NOT EXISTS media (
	id VARCHAR NOT NULL, 
	filename VARCHAR NOT NULL, 
	stored_name VARCHAR NOT NULL, 
	public_token VARCHAR NOT NULL, 
	mime VARCHAR NOT NULL, 
	kind VARCHAR NOT NULL, 
	size INTEGER NOT NULL, 
	width INTEGER, 
	height INTEGER, 
	jpeg_name VARCHAR, 
	alt_text TEXT NOT NULL, 
	endcard_name VARCHAR, 
	sha256 VARCHAR NOT NULL, 
	duration FLOAT, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_media_sha256 ON media (sha256);

CREATE TABLE IF NOT EXISTS oauth_states (
	state VARCHAR NOT NULL, 
	provider VARCHAR NOT NULL, 
	code_verifier VARCHAR NOT NULL, 
	redirect_uri TEXT NOT NULL, 
	created_at DATETIME NOT NULL, 
	PRIMARY KEY (state)
);

CREATE TABLE IF NOT EXISTS posts (
	id VARCHAR NOT NULL, 
	caption TEXT NOT NULL, 
	media_ids JSON NOT NULL, 
	tags JSON NOT NULL, 
	note TEXT NOT NULL, 
	topic_key VARCHAR NOT NULL, 
	campaign VARCHAR NOT NULL, 
	source VARCHAR NOT NULL, 
	idea_id VARCHAR, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id)
);
CREATE INDEX IF NOT EXISTS ix_posts_campaign ON posts (campaign);
CREATE INDEX IF NOT EXISTS ix_posts_topic_key ON posts (topic_key);

CREATE TABLE IF NOT EXISTS settings (
	"key" VARCHAR NOT NULL, 
	value JSON NOT NULL, 
	PRIMARY KEY ("key")
);

CREATE TABLE IF NOT EXISTS comments (
	id VARCHAR NOT NULL, 
	channel_id VARCHAR NOT NULL, 
	external_id VARCHAR NOT NULL, 
	parent_external_id VARCHAR, 
	media_external_id VARCHAR NOT NULL, 
	media_permalink TEXT NOT NULL, 
	media_caption TEXT NOT NULL, 
	author VARCHAR NOT NULL, 
	text TEXT NOT NULL, 
	at DATETIME, 
	like_count INTEGER NOT NULL, 
	is_own BOOLEAN NOT NULL, 
	status VARCHAR NOT NULL, 
	flagged BOOLEAN NOT NULL, 
	assignee VARCHAR NOT NULL, 
	synced_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	CONSTRAINT uq_comment_ext UNIQUE (channel_id, external_id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_comments_at ON comments (at);
CREATE INDEX IF NOT EXISTS ix_comments_channel_id ON comments (channel_id);

CREATE TABLE IF NOT EXISTS post_targets (
	id VARCHAR NOT NULL, 
	post_id VARCHAR NOT NULL, 
	channel_id VARCHAR NOT NULL, 
	caption_override TEXT, 
	media_override JSON, 
	options JSON NOT NULL, 
	schedule_mode VARCHAR NOT NULL, 
	prioritized BOOLEAN NOT NULL, 
	scheduled_at DATETIME, 
	status VARCHAR NOT NULL, 
	override_rate_limit BOOLEAN NOT NULL, 
	attempts INTEGER NOT NULL, 
	next_attempt_at DATETIME, 
	last_error TEXT NOT NULL, 
	provider_state JSON NOT NULL, 
	external_id VARCHAR NOT NULL, 
	permalink TEXT NOT NULL, 
	published_at DATETIME, 
	insights JSON NOT NULL, 
	insights_at DATETIME, 
	locked_at DATETIME, 
	approval JSON NOT NULL, 
	created_at DATETIME NOT NULL, 
	updated_at DATETIME NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(post_id) REFERENCES posts (id) ON DELETE CASCADE, 
	FOREIGN KEY(channel_id) REFERENCES channels (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_post_targets_channel_id ON post_targets (channel_id);
CREATE INDEX IF NOT EXISTS ix_post_targets_post_id ON post_targets (post_id);
CREATE INDEX IF NOT EXISTS ix_post_targets_scheduled_at ON post_targets (scheduled_at);
CREATE INDEX IF NOT EXISTS ix_post_targets_status ON post_targets (status);

CREATE TABLE IF NOT EXISTS slots (
	id INTEGER NOT NULL, 
	channel_id VARCHAR NOT NULL, 
	weekday INTEGER NOT NULL, 
	time VARCHAR NOT NULL, 
	PRIMARY KEY (id), 
	FOREIGN KEY(channel_id) REFERENCES channels (id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_slots_channel_id ON slots (channel_id);

from __future__ import annotations


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin','operator','viewer')),
  created_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  token_hash TEXT NOT NULL UNIQUE,
  created_at_ms INTEGER NOT NULL,
  expires_at_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS replay_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at_ms INTEGER NOT NULL,
  created_by INTEGER NOT NULL REFERENCES users(id),

  target_env_id INTEGER REFERENCES target_environments(id),
  connection_profile_id INTEGER REFERENCES connection_profiles(id),
  log_dir TEXT NOT NULL,
  target_host TEXT NOT NULL,
  target_user TEXT NOT NULL,
  target_command TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('strict-global','parallel-sessions')),

  params_json TEXT,
  metrics_json TEXT,

  run_fingerprint TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('queued','running','paused','failed','success','cancelled')),

  started_at_ms INTEGER,
  finished_at_ms INTEGER,

  verify_ok INTEGER,
  verify_error TEXT,

  last_seq_global_applied INTEGER NOT NULL DEFAULT 0,
  last_checkpoint_sig TEXT,

  entry_mode TEXT,
  via_gateway INTEGER NOT NULL DEFAULT 0,
  gateway_session_id TEXT,
  gateway_endpoint TEXT,
  compliance_status TEXT NOT NULL DEFAULT 'not_applicable',
  compliance_reason TEXT,
  validated_at_ms INTEGER,

  parent_run_id INTEGER REFERENCES replay_runs(id),
  error TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS replay_runs_fingerprint_unique
ON replay_runs(run_fingerprint) WHERE status IN ('queued','running','paused');

CREATE TABLE IF NOT EXISTS replay_run_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES replay_runs(id) ON DELETE CASCADE,
  ts_ms INTEGER NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  data_json TEXT
);

CREATE INDEX IF NOT EXISTS replay_run_events_run_ts
ON replay_run_events(run_id, ts_ms);

CREATE TABLE IF NOT EXISTS replay_failures (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id INTEGER NOT NULL REFERENCES replay_runs(id) ON DELETE CASCADE,
  ts_ms INTEGER NOT NULL,
  session_id TEXT,
  seq_global INTEGER,
  seq_session INTEGER,
  flow_name TEXT,
  event_type TEXT NOT NULL,
  failure_type TEXT NOT NULL,
  severity TEXT NOT NULL CHECK(severity IN ('low','medium','high','critical')),
  expected_value TEXT,
  observed_value TEXT,
  message TEXT NOT NULL,
  evidence_json TEXT
);

CREATE INDEX IF NOT EXISTS replay_failures_run_id
ON replay_failures(run_id, id DESC);

CREATE INDEX IF NOT EXISTS replay_failures_run_type
ON replay_failures(run_id, failure_type);

CREATE INDEX IF NOT EXISTS replay_failures_run_severity
ON replay_failures(run_id, severity);

CREATE TABLE IF NOT EXISTS target_environments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  env_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  host TEXT NOT NULL,
  port INTEGER,
  platform TEXT NOT NULL DEFAULT 'linux',
  transport_hint TEXT NOT NULL DEFAULT 'ssh',
  gateway_required INTEGER NOT NULL DEFAULT 0,
  direct_ssh_policy TEXT NOT NULL DEFAULT 'unrestricted',
  capture_start_mode TEXT NOT NULL DEFAULT 'session_start_required',
  capture_compliance_mode TEXT NOT NULL DEFAULT 'off',
  allow_admin_direct_access INTEGER NOT NULL DEFAULT 0,
  description TEXT,
  metadata_json TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS target_environments_name
ON target_environments(name);

CREATE TABLE IF NOT EXISTS connection_profiles (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  profile_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  transport TEXT NOT NULL DEFAULT 'ssh' CHECK(transport IN ('ssh','telnet')),
  username TEXT,
  port INTEGER,
  command TEXT,
  credential_ref TEXT,
  auth_mode TEXT NOT NULL DEFAULT 'external',
  options_json TEXT,
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS connection_profiles_name
ON connection_profiles(name);

CREATE TABLE IF NOT EXISTS analytics_scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  scope TEXT NOT NULL DEFAULT 'observability',
  visibility TEXT NOT NULL DEFAULT 'private' CHECK(visibility IN ('private','shared')),
  tags_csv TEXT,
  filters_json TEXT NOT NULL,
  created_by INTEGER REFERENCES users(id),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS analytics_scenarios_scope
ON analytics_scenarios(scope, updated_at_ms DESC);

CREATE TABLE IF NOT EXISTS analytics_scenario_favorites (
  scenario_id INTEGER NOT NULL REFERENCES analytics_scenarios(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (scenario_id, user_id)
);

CREATE INDEX IF NOT EXISTS analytics_scenario_favorites_user
ON analytics_scenario_favorites(user_id, created_at_ms DESC);

CREATE TABLE IF NOT EXISTS operational_scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  description TEXT,
  scenario_type TEXT NOT NULL CHECK(scenario_type IN ('replay','stress')),
  squad TEXT,
  area TEXT,
  tags_csv TEXT,
  owner_name TEXT,
  owner_contact TEXT,
  sla_max_failure_rate_pct REAL,
  sla_max_criticality_score REAL,
  target_env_id INTEGER REFERENCES target_environments(id),
  connection_profile_id INTEGER REFERENCES connection_profiles(id),
  log_dir TEXT NOT NULL,
  target_host TEXT NOT NULL,
  target_user TEXT NOT NULL,
  target_command TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('strict-global','parallel-sessions')),
  params_json TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at_ms INTEGER NOT NULL,
  updated_at_ms INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS operational_scenarios_type
ON operational_scenarios(scenario_type, updated_at_ms DESC);

CREATE TABLE IF NOT EXISTS operational_scenario_favorites (
  scenario_id INTEGER NOT NULL REFERENCES operational_scenarios(id) ON DELETE CASCADE,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (scenario_id, user_id)
);

CREATE INDEX IF NOT EXISTS operational_scenario_favorites_user
ON operational_scenario_favorites(user_id, created_at_ms DESC);

CREATE INDEX IF NOT EXISTS sessions_user_id
ON sessions(user_id);

CREATE INDEX IF NOT EXISTS replay_runs_status
ON replay_runs(status);

CREATE INDEX IF NOT EXISTS replay_runs_created_by
ON replay_runs(created_by);

CREATE INDEX IF NOT EXISTS replay_runs_created_at_ms
ON replay_runs(created_at_ms DESC);

CREATE INDEX IF NOT EXISTS replay_run_events_kind
ON replay_run_events(kind);

CREATE TABLE IF NOT EXISTS gateway_state (
  id INTEGER PRIMARY KEY CHECK(id = 1),
  active INTEGER NOT NULL DEFAULT 0,
  activated_at_ms INTEGER,
  activated_by_id INTEGER REFERENCES users(id),
  activated_by_username TEXT,
  deactivated_at_ms INTEGER,
  deactivated_by_username TEXT,
  environment_json TEXT,
  connection_profile_id INTEGER REFERENCES connection_profiles(id),
  operational_user_id INTEGER REFERENCES users(id),
  capture_enabled INTEGER NOT NULL DEFAULT 0,
  updated_at_ms INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS capture_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_uuid TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','finished','interrupted','failed')),
  created_by INTEGER NOT NULL REFERENCES users(id),
  created_by_username TEXT NOT NULL,
  started_at_ms INTEGER NOT NULL,
  ended_at_ms INTEGER,
  environment_json TEXT,
  connection_profile_id INTEGER REFERENCES connection_profiles(id),
  connection_profile_name TEXT,
  operational_user_id INTEGER REFERENCES users(id),
  gateway_state_snapshot_json TEXT,
  log_dir TEXT NOT NULL,
  target_env_id INTEGER REFERENCES target_environments(id),
  notes TEXT
);

CREATE INDEX IF NOT EXISTS capture_sessions_status
ON capture_sessions(status, started_at_ms DESC);

CREATE INDEX IF NOT EXISTS capture_sessions_created_by
ON capture_sessions(created_by, started_at_ms DESC);
"""


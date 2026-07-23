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
  capture_scope_json TEXT,
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
  notes TEXT,
  session_count INTEGER NOT NULL DEFAULT 0,
  event_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS capture_sessions_status
ON capture_sessions(status, started_at_ms DESC);

CREATE INDEX IF NOT EXISTS capture_sessions_created_by
ON capture_sessions(created_by, started_at_ms DESC);

-- Synthetic data & source analysis tables

CREATE TABLE IF NOT EXISTS screens (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  screen_signature TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL DEFAULT '',
  program_name TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS screens_signature ON screens(screen_signature);

CREATE TABLE IF NOT EXISTS screen_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  screen_id INTEGER NOT NULL REFERENCES screens(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  prompt TEXT NOT NULL DEFAULT '',
  datatype TEXT NOT NULL DEFAULT 'text',
  required INTEGER NOT NULL DEFAULT 0,
  unique_flag INTEGER NOT NULL DEFAULT 0,
  lookup_table TEXT,
  constraints_json TEXT
);

CREATE INDEX IF NOT EXISTS screen_fields_screen ON screen_fields(screen_id);

CREATE TABLE IF NOT EXISTS synthetic_datasets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  screen_id INTEGER NOT NULL DEFAULT 0,
  entity_name TEXT NOT NULL DEFAULT '',
  quantity INTEGER NOT NULL DEFAULT 0,
  seed INTEGER NOT NULL DEFAULT 0,
  params_json TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS synthetic_datasets_screen ON synthetic_datasets(screen_id);
CREATE INDEX IF NOT EXISTS synthetic_datasets_name ON synthetic_datasets(name);

CREATE TABLE IF NOT EXISTS synthetic_records (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  dataset_id INTEGER NOT NULL REFERENCES synthetic_datasets(id) ON DELETE CASCADE,
  record_index INTEGER NOT NULL,
  data_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS synthetic_records_dataset ON synthetic_records(dataset_id, record_index);

-- Entity tests: validacoes CRUD por entidade (nao sao jornadas de negocio)
CREATE TABLE IF NOT EXISTS entity_tests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_name TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  steps_json TEXT NOT NULL DEFAULT '[]',
  tags_csv TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS entity_tests_entity ON entity_tests(entity_name);

CREATE TABLE IF NOT EXISTS source_entities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  storage_type TEXT NOT NULL DEFAULT 'unknown',
  source TEXT NOT NULL DEFAULT '',
  metadata_json TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS source_entities_name ON source_entities(name);

CREATE TABLE IF NOT EXISTS source_entity_fields (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_id INTEGER NOT NULL REFERENCES source_entities(id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  datatype TEXT NOT NULL DEFAULT 'text',
  required INTEGER NOT NULL DEFAULT 0,
  unique_flag INTEGER NOT NULL DEFAULT 0,
  constraints_json TEXT
);

CREATE INDEX IF NOT EXISTS source_entity_fields_entity ON source_entity_fields(entity_id);

-- Audit trails (inferencia IA rastreavel e persistida)

CREATE TABLE IF NOT EXISTS audit_trails (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entity_name TEXT NOT NULL DEFAULT '',
  field_name TEXT NOT NULL DEFAULT '',
  inference_type TEXT NOT NULL DEFAULT '',
  final_decision TEXT NOT NULL DEFAULT '',
  confidence REAL NOT NULL DEFAULT 0.0,
  evidence_json TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS audit_trails_entity ON audit_trails(entity_name);

-- Journey reports (justificativas das decisoes de geracao)

CREATE TABLE IF NOT EXISTS journey_reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id TEXT NOT NULL,
  entity_name TEXT NOT NULL,
  generated INTEGER NOT NULL DEFAULT 0,
  report_json TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS journey_reports_journey ON journey_reports(journey_id);

-- Business rule evaluation (persisted results with normalization)

CREATE TABLE IF NOT EXISTS business_evals (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_hash TEXT NOT NULL DEFAULT '',
  source_dir TEXT NOT NULL DEFAULT '',
  rules_evaluated INTEGER NOT NULL DEFAULT 0,
  rules_ok INTEGER NOT NULL DEFAULT 0,
  rules_broken INTEGER NOT NULL DEFAULT 0,
  gaps_json TEXT NOT NULL DEFAULT '[]',
  flows_coverage_json TEXT NOT NULL DEFAULT '[]',
  recommendation TEXT NOT NULL DEFAULT '',
  entities_normalized_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS business_evals_hash ON business_evals(source_hash);

-- Business gaps (gaps de negocio detectados com status e acao corretiva)

CREATE TABLE IF NOT EXISTS business_gaps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  gap_id TEXT NOT NULL,
  severity TEXT NOT NULL DEFAULT 'high' CHECK(severity IN ('critical','high','medium','low')),
  description TEXT NOT NULL DEFAULT '',
  missing_entity TEXT NOT NULL DEFAULT '',
  affected_flow TEXT NOT NULL DEFAULT '',
  impact TEXT NOT NULL DEFAULT '',
  recommendation TEXT NOT NULL DEFAULT '',
  suggested_files TEXT NOT NULL DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','investigating','resolved','dismissed')),
  resolved_at TEXT DEFAULT NULL,
  resolved_notes TEXT DEFAULT NULL,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS business_gaps_status ON business_gaps(status);
CREATE INDEX IF NOT EXISTS business_gaps_entity ON business_gaps(missing_entity);

-- Journey / jornada tables

CREATE TABLE IF NOT EXISTS journeys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  category TEXT NOT NULL DEFAULT '',
  entry_screen TEXT NOT NULL DEFAULT '',
  steps_json TEXT NOT NULL DEFAULT '[]',
  dataset_bindings_json TEXT NOT NULL DEFAULT '{}',
  tags_csv TEXT NOT NULL DEFAULT '',
  metadata_json TEXT,
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS journeys_journey_id ON journeys(journey_id);
CREATE INDEX IF NOT EXISTS journeys_category ON journeys(category);

CREATE TABLE IF NOT EXISTS journey_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id TEXT NOT NULL REFERENCES journeys(journey_id) ON DELETE CASCADE,
  session_index INTEGER NOT NULL,
  data_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'completed' CHECK(status IN ('queued','running','completed','failed')),
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT
);

CREATE INDEX IF NOT EXISTS journey_sessions_journey ON journey_sessions(journey_id, session_index);

-- Scheduler / regression tables

CREATE TABLE IF NOT EXISTS synthetic_schedules (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id TEXT NOT NULL UNIQUE,
  journey_id TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '',
  interval_hours INTEGER NOT NULL DEFAULT 24,
  session_count INTEGER NOT NULL DEFAULT 10,
  seed INTEGER NOT NULL DEFAULT 0,
  concurrency INTEGER NOT NULL DEFAULT 5,
  enabled INTEGER NOT NULL DEFAULT 1,
  alert_threshold_pct REAL NOT NULL DEFAULT 10.0,
  created_at TEXT NOT NULL DEFAULT '',
  last_run_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS synthetic_schedules_journey ON synthetic_schedules(journey_id);

CREATE TABLE IF NOT EXISTS synthetic_schedule_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schedule_id TEXT NOT NULL REFERENCES synthetic_schedules(schedule_id) ON DELETE CASCADE,
  total_sessions INTEGER NOT NULL DEFAULT 0,
  completed INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0,
  success_rate_pct REAL NOT NULL DEFAULT 0.0,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  error_summary_json TEXT,
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS synthetic_schedule_runs_schedule ON synthetic_schedule_runs(schedule_id, id DESC);

-- Snapshot baseline (golden screens)

CREATE TABLE IF NOT EXISTS screen_baselines (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  journey_id TEXT NOT NULL,
  baseline_name TEXT NOT NULL DEFAULT 'default',
  step_order INTEGER NOT NULL,
  screen_signature TEXT NOT NULL DEFAULT '',
  screen_text_hash TEXT NOT NULL DEFAULT '',
  screen_text TEXT NOT NULL DEFAULT '',
  captured_at TEXT NOT NULL DEFAULT '',
  tags_json TEXT NOT NULL DEFAULT '[]',
  UNIQUE(journey_id, baseline_name, step_order)
);

CREATE INDEX IF NOT EXISTS screen_baselines_journey ON screen_baselines(journey_id, baseline_name);

-- Pipeline async execution tracking

CREATE TABLE IF NOT EXISTS pipeline_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL UNIQUE,
  source_dir TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
  phase TEXT NOT NULL DEFAULT '',
  step TEXT NOT NULL DEFAULT '',
  progress_pct INTEGER NOT NULL DEFAULT 0,
  entities_found INTEGER NOT NULL DEFAULT 0,
  screens_found INTEGER NOT NULL DEFAULT 0,
  journeys_found INTEGER NOT NULL DEFAULT 0,
  datasets_found INTEGER NOT NULL DEFAULT 0,
  result_json TEXT,
  error_message TEXT,
  started_at TEXT NOT NULL DEFAULT '',
  finished_at TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS pipeline_runs_run_id ON pipeline_runs(run_id);

-- Screen-Entity Bindings (P2-A: Synthetic Knowledge Base)

CREATE TABLE IF NOT EXISTS screen_entity_bindings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  screen_title TEXT NOT NULL DEFAULT '',
  program_name TEXT NOT NULL DEFAULT '',
  source_file TEXT NOT NULL DEFAULT '',
  source_line_start INTEGER NOT NULL DEFAULT 0,
  source_line_end INTEGER NOT NULL DEFAULT 0,
  entity_name TEXT NOT NULL DEFAULT '',
  operation TEXT NOT NULL DEFAULT '',
  matched_fields_json TEXT NOT NULL DEFAULT '[]',
  unmatched_fields_json TEXT NOT NULL DEFAULT '[]',
  confidence REAL NOT NULL DEFAULT 0.0,
  evidence_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS screen_entity_bindings_entity ON screen_entity_bindings(entity_name);
CREATE INDEX IF NOT EXISTS screen_entity_bindings_confidence ON screen_entity_bindings(confidence);
"""


-- Run after every bulk ingest. DuckDB FTS indexes are snapshots and do not
-- auto-update on INSERT/UPDATE/DELETE.
PRAGMA drop_fts_index('assets');
PRAGMA create_fts_index('assets', 'asset_path', 'tokens', 'logical_id');

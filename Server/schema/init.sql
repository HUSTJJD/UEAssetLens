-- UEAssetLens (UAL) DuckDB storage schema, v3.0
-- v3.0 keeps the 16-key Core Record frozen and adds a separate typed
-- extension_payload table (Phase 1). Four-place contract sync:
--   PROTOCOL.md, init.sql, UALTypes.h, db.py.
-- Verified against DuckDB 2.0.0 on 2026-11-15:
--   FLOAT[3] / FLOAT[16] fixed arrays, VARCHAR[] lists, MAP(VARCHAR,VARCHAR),
--   PRAGMA create_fts_index and array_cosine_similarity() all work.
-- Requires: DuckDB >= 1.1 for array_cosine_similarity (fallback for older builds:
--   list_cosine_similarity(fp::FLOAT[], target::FLOAT[])).
--
-- NOTE ON FTS: DuckDB's FTS index is a snapshot. It is NOT maintained on INSERT.
-- After any bulk ingest (ual-cli build / YAML import) you MUST rebuild it:
--   PRAGMA drop_fts_index('assets');
--   PRAGMA create_fts_index('assets', 'asset_path', 'tokens', 'logical_id');
-- See rebuild_fts.sql.

CREATE TABLE IF NOT EXISTS schema_meta (
    key         VARCHAR PRIMARY KEY,
    value       VARCHAR NOT NULL
);
INSERT OR REPLACE INTO schema_meta VALUES ('schema_version', '3.0');
INSERT OR REPLACE INTO schema_meta VALUES ('fingerprint_dims', '16');
INSERT OR REPLACE INTO schema_meta VALUES ('engine_version', 'UE6');

-- 1. Main asset spec + deterministic fingerprint table.
CREATE TABLE IF NOT EXISTS assets (
    asset_path        VARCHAR PRIMARY KEY,
    logical_id        VARCHAR NOT NULL UNIQUE,   -- e.g. AnimMontage:AM_Knight_HeavySlash_02
    asset_class       VARCHAR NOT NULL,
    parent_class      VARCHAR NOT NULL,
    taxonomy          VARCHAR[],                 -- ['Asset', 'Animation', 'Montage']
    tokens            VARCHAR,                   -- camel/path/parent-context terms, fed to FTS
    content_hash      VARCHAR NOT NULL,          -- change detection ONLY, never used for dedup

    -- Flat hard-constraint filters (millisecond pre-filtering before vector scan)
    duration          FLOAT DEFAULT 0.0,
    bounds_size       FLOAT[3],                  -- [SizeX, SizeY, SizeZ]
    skeleton_path     VARCHAR,
    dominant_limb     VARCHAR,

    -- Deterministic 16-dim mathematical fingerprint for geometry/kinematics/params
    fingerprint_vec   FLOAT[16],

    -- Full structured payload. Values are serialized strings by convention:
    --   scalars -> plain text, vectors -> "[a,b,c]", lists/objects -> YAML text fragment.
    -- MAP(VARCHAR,VARCHAR) erases numeric types; readers must parse by key contract.
    interface_spec    MAP(VARCHAR, VARCHAR),
    domain_spec       MAP(VARCHAR, VARCHAR),
    yaml_cache        VARCHAR,                   -- pre-rendered annotated YAML spec
    updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_assets_class    ON assets(asset_class);
CREATE INDEX IF NOT EXISTS idx_assets_skeleton ON assets(skeleton_path);
CREATE INDEX IF NOT EXISTS idx_assets_duration ON assets(duration);
CREATE INDEX IF NOT EXISTS idx_assets_limb     ON assets(dominant_limb);

-- 2. Bidirectional dependency + co-occurrence graph edges.
CREATE TABLE IF NOT EXISTS asset_edges (
    source_path       VARCHAR NOT NULL,
    target_path       VARCHAR NOT NULL,
    edge_type         VARCHAR NOT NULL,          -- 'DEPENDS_ON' | 'CO_OCCURS'
    weight            FLOAT DEFAULT 1.0,
    PRIMARY KEY (source_path, target_path, edge_type)
);
CREATE INDEX IF NOT EXISTS idx_edges_source ON asset_edges(source_path, edge_type);

-- 2a. Typed extension payload (Phase 1). The Core Record above stays exactly
-- 16 staging keys. Field specs come from Config/UAL/extensions/*.yaml; values
-- are projected from existing MAP columns, never from a 17th staging key.
-- No updated_at / timestamp / local absolute path / random id is stored here.
CREATE TABLE IF NOT EXISTS extension_payload (
    asset_path        VARCHAR NOT NULL,
    namespace         VARCHAR NOT NULL,
    field_name        VARCHAR NOT NULL,
    value_type        VARCHAR NOT NULL,            -- string|integer|number|boolean|yaml
    value_text        VARCHAR,
    value_number      DOUBLE,
    value_bool        BOOLEAN,
    provenance        VARCHAR NOT NULL,            -- builtin|plugin|project|user
    PRIMARY KEY (asset_path, namespace, field_name)
);
CREATE INDEX IF NOT EXISTS idx_extension_field ON extension_payload(namespace, field_name);

-- 2b. Search projection. forward_cm / strike_window live in domain_spec as
-- serialized strings; this view exposes them as filterable columns so the
-- physical DDL stays identical to the v2.0 specification.
CREATE OR REPLACE VIEW v_search AS
SELECT a.*,
       COALESCE(TRY_CAST(a.domain_spec['forward_cm'] AS DOUBLE), 0.0) AS forward_cm,
       COALESCE(a.domain_spec['strike_window'], '')                    AS strike_window,
       COALESCE(a.domain_spec['hint'], '')                             AS hint
FROM assets a;

-- 3. Full-text index (BM25) over the token stream.
PRAGMA create_fts_index('assets', 'asset_path', 'tokens', 'logical_id');

-- Reference queries ---------------------------------------------------------------
-- (a) Hard filters + FTS BM25 ranking:
--   SELECT a.asset_path, fts_main_assets.match_bm25(a.asset_path, 'heavy slash') AS score
--   FROM assets a
--   WHERE a.asset_class = 'AnimMontage'
--     AND a.duration BETWEEN 0.8 AND 1.5
--     AND a.skeleton_path LIKE '%SK_Knight%'
--   ORDER BY score DESC LIMIT 8;
--
-- (b) Duplicate detection via a single SQL cosine self-join:
--   WITH target AS (SELECT fingerprint_vec AS v FROM assets WHERE asset_path = ?)
--   SELECT a.asset_path,
--          array_cosine_similarity(a.fingerprint_vec, target.v) AS similarity
--   FROM assets a CROSS JOIN target
--   WHERE a.asset_path <> ? AND a.fingerprint_vec IS NOT NULL
--     AND similarity >= 0.85
--   ORDER BY similarity DESC;
--
-- (c) Co-occurrence bundle:
--   SELECT e.target_path, a.asset_class, a.logical_id, e.weight
--   FROM asset_edges e JOIN assets a ON a.asset_path = e.target_path
--   WHERE e.source_path = ? AND e.edge_type = 'CO_OCCURS'
--   ORDER BY e.weight DESC LIMIT ?;

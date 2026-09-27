-- Initial schema (Spec 02 §3). Contract fields keep their contract names.
CREATE TABLE requests (
    id TEXT PRIMARY KEY,
    message TEXT NOT NULL,
    source_area TEXT,
    message_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'classified', 'failed')),
    claim_token TEXT,
    categoria TEXT CHECK (categoria IN ('bug', 'datos', 'acceso', 'automatizacion', 'consulta', 'otro')),
    prioridad TEXT CHECK (prioridad IN ('alta', 'media', 'baja')),
    area_sugerida TEXT CHECK (area_sugerida IN ('backend', 'frontend', 'data', 'devops', 'producto', 'digital_transformation')),
    idioma TEXT CHECK (idioma IN ('es', 'pt')),
    resumen TEXT,
    requiere_info INTEGER CHECK (requiere_info IN (0, 1)),
    pregunta_seguimiento TEXT,
    confianza REAL CHECK (confianza BETWEEN 0 AND 1),
    version_prompt TEXT,
    provider TEXT,
    model TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0 CHECK (reviewed IN (0, 1)),
    corrected INTEGER NOT NULL DEFAULT 0 CHECK (corrected IN (0, 1)),
    original_classification TEXT CHECK (original_classification IS NULL OR json_valid(original_classification)),
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        status <> 'classified' OR (
            categoria IS NOT NULL AND prioridad IS NOT NULL AND area_sugerida IS NOT NULL
            AND idioma IS NOT NULL AND resumen IS NOT NULL AND requiere_info IS NOT NULL
            AND confianza IS NOT NULL AND version_prompt IS NOT NULL AND provider IS NOT NULL
            AND model IS NOT NULL AND original_classification IS NOT NULL
        )
    )
);

CREATE INDEX idx_requests_categoria ON requests (categoria);
CREATE INDEX idx_requests_prioridad ON requests (prioridad);
CREATE INDEX idx_requests_area_sugerida ON requests (area_sugerida);
CREATE INDEX idx_requests_status ON requests (status);
CREATE INDEX idx_requests_created ON requests (created_at, id);

CREATE TABLE corrections (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL REFERENCES requests (id),
    previous_values TEXT NOT NULL CHECK (json_valid(previous_values)),
    new_values TEXT NOT NULL CHECK (json_valid(new_values)),
    author TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX idx_corrections_request ON corrections (request_id, id);

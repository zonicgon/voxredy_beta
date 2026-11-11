-- =====================================================================
-- VoxReady · Modelo de datos v2.1 · PostgreSQL 14 o superior (probado en 16)
-- ---------------------------------------------------------------------
-- v2.1 = v2.0 + lo que necesita la página v56 (maqueta de la base):
--   * retención configurable por organización (días y "solo notas") y por escenario
--   * escenarios: inactivo / archivado, de qué tema de ejemplo viene y si se modificó
--   * sesión "abandoned" (entrevista cortada a mitad) y título del escenario guardado
--   * agenda de la próxima sesión (app_user.next_session_date)
--   * tablas deletion_request (solicitudes de borrado) y password_reset (códigos)
-- Se mantienen las decisiones de la v2.0: no se guarda audio ni video, notas de 0 a 5,
-- aislamiento por client_id, purga diaria de lo vencido.
--
-- OJO: este script BORRA las tablas de VoxReady si ya existen (por ejemplo, si ya
-- corriste la v2.0) y las crea de nuevo. Úsalo antes de conectar la página, cuando
-- todavía no hay datos reales.
-- =====================================================================

BEGIN;

-- ---------------------------------------------------------------------
-- 0. Limpieza (para poder correrlo aunque ya exista la v2.0)
-- ---------------------------------------------------------------------
DROP VIEW  IF EXISTS v_user_area_progress;
DROP TABLE IF EXISTS labeling_correction, labeling_case, area_score, report,
                     password_reset, deletion_request, session,
                     scenario_item, scenario, app_user, client CASCADE;
DROP FUNCTION IF EXISTS purge_expired_sessions();
DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

-- Utilidad: mantiene updated_at al día en cada UPDATE
CREATE FUNCTION set_updated_at() RETURNS trigger AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- 1. Identidad y organizaciones
-- =====================================================================

-- Organización cliente (tenant). Raíz del aislamiento de datos.
-- La política de retención la define su administrador (pantalla a3).
CREATE TABLE client (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name            varchar(150) NOT NULL,
    status          varchar(20)  NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended')),
    retention_days  int          NOT NULL DEFAULT 30 CHECK (retention_days BETWEEN 1 AND 365),
    retention_mode  varchar(20)  NOT NULL DEFAULT 'full'
                    CHECK (retention_mode IN ('full', 'scores_only')),   -- página: completo / solo_notas
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now()
);

-- Usuarios. Un usuario pertenece a una sola organización
-- (client_id es nulo solo para el configurador maestro, que es transversal).
CREATE TABLE app_user (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id          uuid REFERENCES client(id),
    email              varchar(256) NOT NULL,
    display_name       varchar(150) NOT NULL,
    first_name         varchar(80),
    last_name          varchar(80),
    password_hash      varchar(255) NOT NULL,          -- bcrypt/argon2 calculado en el servidor (la sal va dentro del hash)
    role               varchar(20)  NOT NULL
                       CHECK (role IN ('spokesperson', 'client_admin', 'master_config')),  -- página: vocero / admin
    status             varchar(20)  NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'deactivated')),
    next_session_date  date,                            -- agenda «Próxima sesión» (Mi progreso)
    last_login_at      timestamptz,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT app_user_client_por_rol CHECK (
        (role = 'master_config' AND client_id IS NULL) OR
        (role <> 'master_config' AND client_id IS NOT NULL)
    )
);
CREATE UNIQUE INDEX app_user_email_unico ON app_user (lower(email));
CREATE INDEX app_user_client ON app_user (client_id);

-- Códigos para recuperar la contraseña (se mandan por correo; aquí solo su hash)
CREATE TABLE password_reset (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    code_hash   varchar(255) NOT NULL,
    attempts    int NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
    expires_at  timestamptz NOT NULL DEFAULT (now() + interval '15 minutes'),
    used_at     timestamptz,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX password_reset_usuario ON password_reset (user_id, expires_at DESC);

-- =====================================================================
-- 2. Escenarios de crisis (los configura el administrador de la organización)
-- =====================================================================
CREATE TABLE scenario (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id         uuid NOT NULL REFERENCES client(id),
    slug              varchar(40)  NOT NULL,           -- id que usa la app (derrame, retiro, datos, o uno propio)
    template_slug     varchar(40),                     -- tema de ejemplo de VoxReady del que viene (nulo = propio)
    is_modified       boolean      NOT NULL DEFAULT false,  -- un tema de ejemplo editado por la organización
    title             varchar(150) NOT NULL,
    icon              varchar(10),
    category          varchar(20)  NOT NULL
                      CHECK (category IN ('health', 'reputational', 'operational')),      -- Sanitaria / Reputacional / Operativa
    audience          varchar(50)  NOT NULL,           -- público interno: Dirección, Planta, Técnicos
    difficulty        varchar(20)  NOT NULL
                      CHECK (difficulty IN ('basic', 'intermediate', 'hard')),            -- Intermedio / Difícil
    estimated_minutes int          NOT NULL CHECK (estimated_minutes > 0),
    question_count    int          NOT NULL DEFAULT 8 CHECK (question_count BETWEEN 1 AND 20),
    languages         varchar(20)  NOT NULL DEFAULT 'ES',
    summary           varchar(500) NOT NULL,           -- texto corto de la tarjeta
    context           text         NOT NULL CHECK (length(context) <= 1500),  -- lo que lee el vocero
    optics            varchar(20)  NOT NULL
                      CHECK (optics IN ('empathetic', 'formal', 'technical')),            -- Empática / Formal / Técnica
    optics_detail     varchar(300),
    interviewer_data  jsonb        NOT NULL DEFAULT '{}'::jsonb,  -- escena, afectados, consecuencia, vocabulario (no lo ve el vocero)
    retention_days    int          CHECK (retention_days IN (30, 90, 180)),   -- nulo = la de la organización
    sort_order        int          NOT NULL DEFAULT 50,
    status            varchar(20)  NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'inactive', 'archived')),  -- archivado = «eliminado» (se conservan sus sesiones)
    created_by        uuid REFERENCES app_user(id) ON DELETE SET NULL,
    created_at        timestamptz  NOT NULL DEFAULT now(),
    updated_at        timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT scenario_slug_unico UNIQUE (client_id, slug),
    CONSTRAINT scenario_ejemplo_modificado CHECK (template_slug IS NOT NULL OR NOT is_modified)
);
CREATE INDEX scenario_client_status ON scenario (client_id, status, sort_order);

-- Mensajes clave, líneas rojas y datos del dossier del periodista
CREATE TABLE scenario_item (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    scenario_id  uuid NOT NULL REFERENCES scenario(id) ON DELETE CASCADE,
    kind         varchar(20)  NOT NULL
                 CHECK (kind IN ('key_message', 'red_line', 'dossier')),  -- página: mensaje_clave / linea_roja / dossier
    text         varchar(1000) NOT NULL,
    sort_order   int NOT NULL DEFAULT 0,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX scenario_item_scenario ON scenario_item (scenario_id, kind, sort_order);

-- =====================================================================
-- 3. Sesiones de práctica (una sesión = una entrevista)
-- No hay columnas para audio ni video: no se almacenan.
-- =====================================================================
CREATE TABLE session (
    id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id              uuid NOT NULL REFERENCES client(id),
    user_id                uuid NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    scenario_id            uuid NOT NULL REFERENCES scenario(id),
    scenario_title         varchar(150) NOT NULL,       -- el nombre tal como era al practicar
    status                 varchar(20) NOT NULL DEFAULT 'created'
                           CHECK (status IN ('created', 'consented', 'in_progress', 'analyzing', 'completed', 'abandoned', 'failed')),
                           -- página: en_curso = in_progress/analyzing · incompleta = abandoned · completa = completed
    -- consentimiento (se registra antes de empezar)
    consent_accepted       boolean NOT NULL DEFAULT false,
    consent_text_version   varchar(20),
    consented_at           timestamptz,
    -- desarrollo de la entrevista
    started_at             timestamptz,
    finished_at            timestamptz,
    calibrated             boolean NOT NULL DEFAULT false,
    interviewee            jsonb,                                -- {"first_name": …, "last_name": …}
    transcript             jsonb NOT NULL DEFAULT '[]'::jsonb,   -- [{role, content}, …] se guarda pregunta por pregunta
    voice_metrics          jsonb,
    face_summary           jsonb,
    app_version            varchar(10),
    -- retención: la fecha se calcula al crear la sesión con la política vigente
    retention_mode         varchar(20) NOT NULL DEFAULT 'full' CHECK (retention_mode IN ('full', 'scores_only')),
    expires_at             timestamptz NOT NULL DEFAULT (now() + interval '30 days'),
    created_at             timestamptz NOT NULL DEFAULT now(),
    updated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT session_consentimiento_previo CHECK (
        started_at IS NULL OR (consent_accepted AND consented_at IS NOT NULL AND consented_at <= started_at)
    ),
    CONSTRAINT session_estado_con_consentimiento CHECK (
        status IN ('created', 'failed') OR consent_accepted
    ),
    -- con «solo notas», la transcripción de una sesión terminada queda vacía
    CONSTRAINT session_solo_notas_sin_transcripcion CHECK (
        retention_mode = 'full' OR status <> 'completed' OR transcript = '[]'::jsonb
    )
);
CREATE INDEX session_user_fecha ON session (user_id, created_at DESC);
CREATE INDEX session_client_fecha ON session (client_id, created_at DESC);
CREATE INDEX session_expira ON session (expires_at);

-- =====================================================================
-- 4. Informes y puntajes
-- =====================================================================

-- Informe coach de una sesión. Re-evaluar crea una versión nueva sin borrar la anterior.
CREATE TABLE report (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id               uuid NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    version_no               int  NOT NULL DEFAULT 1 CHECK (version_no >= 1),
    is_current               boolean NOT NULL DEFAULT true,
    overall_score            numeric(2,1) CHECK (overall_score BETWEEN 0 AND 5),  -- nulo si no hubo datos
    summary                  text,
    main_strength            text,
    main_recommendation      text,
    priority_reason          text,
    judges                   jsonb NOT NULL DEFAULT '{}'::jsonb,  -- informes de contenido, voz e imagen (con «solo notas»: solo las notas)
    caps_applied             jsonb NOT NULL DEFAULT '[]'::jsonb,
    sources_available        text[] NOT NULL DEFAULT '{}',
    final_report             jsonb NOT NULL DEFAULT '{}'::jsonb,  -- informe completo tal como lo muestra la página
    -- trazabilidad
    rubric_version           varchar(20) NOT NULL,                -- p. ej. la versión de app.py
    ai_provider              varchar(30) NOT NULL,
    ai_model                 varchar(100),
    -- revisión humana
    low_confidence           boolean NOT NULL DEFAULT false,
    generated_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT report_version_unica UNIQUE (session_id, version_no)
);
CREATE UNIQUE INDEX report_vigente_unico ON report (session_id) WHERE is_current;

-- Puntaje por cada una de las 4 áreas finales del informe
CREATE TABLE area_score (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id   uuid NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    area_key    varchar(20) NOT NULL
                CHECK (area_key IN ('expression', 'voice_tone', 'coherence', 'empathy')),
                -- página: Expresión / Tono de voz / Coherencia / Empatía
    score       numeric(2,1) CHECK (score BETWEEN 0 AND 5),   -- nulo = "sin datos"
    sources     text[] NOT NULL DEFAULT '{}',
    comment     text,
    to_work_on  boolean NOT NULL DEFAULT false,
    level       varchar(20) CHECK (level IN ('high_priority', 'reinforce', 'fine_tune')),
    exercise    text,
    CONSTRAINT area_score_unica UNIQUE (report_id, area_key)
);

-- =====================================================================
-- 5. Solicitudes de borrado de cuenta (las pide el vocero, las procesa el admin)
-- Al procesarla se borra el usuario (sus sesiones e informes caen en cascada) y
-- aquí queda constancia SIN datos personales (user_id pasa a nulo, user_label se vacía).
-- =====================================================================
CREATE TABLE deletion_request (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    client_id     uuid NOT NULL REFERENCES client(id),
    user_id       uuid REFERENCES app_user(id) ON DELETE SET NULL,
    user_label    varchar(300),                -- «Nombre Apellido (correo)» mientras está pendiente
    status        varchar(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processed')),
    requested_at  timestamptz NOT NULL DEFAULT now(),
    processed_at  timestamptz,
    processed_by  uuid REFERENCES app_user(id) ON DELETE SET NULL,
    CONSTRAINT deletion_request_procesada CHECK (
        (status = 'pending'   AND processed_at IS NULL AND user_id IS NOT NULL) OR
        (status = 'processed' AND processed_at IS NOT NULL AND user_label IS NULL)
    )
);
CREATE UNIQUE INDEX deletion_request_una_pendiente ON deletion_request (user_id) WHERE status = 'pending';
CREATE INDEX deletion_request_client ON deletion_request (client_id, status, requested_at DESC);

-- =====================================================================
-- 6. Revisión humana de casos de baja confianza (configurador maestro, m3)
-- =====================================================================
CREATE TABLE labeling_case (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    report_id    uuid NOT NULL REFERENCES report(id) ON DELETE CASCADE,
    reason       varchar(20) NOT NULL CHECK (reason IN ('low_confidence', 'borderline', 'random')),
    status       varchar(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'resolved')),
    resolved_by  uuid REFERENCES app_user(id),
    resolved_at  timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT labeling_case_resuelto CHECK (
        (status = 'pending'  AND resolved_by IS NULL AND resolved_at IS NULL) OR
        (status = 'resolved' AND resolved_by IS NOT NULL AND resolved_at IS NOT NULL)
    )
);
CREATE INDEX labeling_case_pendientes ON labeling_case (status, created_at);

CREATE TABLE labeling_correction (
    id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    labeling_case_id  uuid NOT NULL REFERENCES labeling_case(id) ON DELETE CASCADE,
    area_key          varchar(20) NOT NULL CHECK (area_key IN ('expression', 'voice_tone', 'coherence', 'empathy')),
    ai_score          numeric(2,1) CHECK (ai_score BETWEEN 0 AND 5),
    corrected_score   numeric(2,1) NOT NULL CHECK (corrected_score BETWEEN 0 AND 5),
    comment           varchar(1000),
    corrected_by      uuid NOT NULL REFERENCES app_user(id),
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT labeling_correction_unica UNIQUE (labeling_case_id, area_key)
);

-- =====================================================================
-- 7. Triggers de updated_at
-- =====================================================================
CREATE TRIGGER t_client_upd        BEFORE UPDATE ON client        FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER t_app_user_upd      BEFORE UPDATE ON app_user      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER t_scenario_upd      BEFORE UPDATE ON scenario      FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER t_session_upd       BEFORE UPDATE ON session       FOR EACH ROW EXECUTE FUNCTION set_updated_at();
CREATE TRIGGER t_labeling_case_upd BEFORE UPDATE ON labeling_case FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- =====================================================================
-- 8. Purga diaria (la llama el backend: Vercel Cron → /api/tareas/purgar)
-- Borra sesiones vencidas (informes y puntajes caen en cascada), entrevistas
-- abandonadas sin ninguna respuesta y códigos de contraseña vencidos.
-- Devuelve cuántas sesiones se borraron.
-- =====================================================================
CREATE FUNCTION purge_expired_sessions() RETURNS integer AS $$
DECLARE
    borradas integer;
BEGIN
    DELETE FROM session
     WHERE expires_at < now()
        OR (status = 'abandoned' AND NOT jsonb_path_exists(transcript, '$[*] ? (@.role == "user")'));
    GET DIAGNOSTICS borradas = ROW_COUNT;
    DELETE FROM password_reset WHERE expires_at < now() OR used_at IS NOT NULL;
    RETURN borradas;
END;
$$ LANGUAGE plpgsql;

-- =====================================================================
-- 9. Vista de apoyo: progreso del vocero por área (Mi progreso)
-- =====================================================================
CREATE VIEW v_user_area_progress AS
SELECT s.client_id,
       s.user_id,
       a.area_key,
       count(*)                                           AS sessions_count,
       round(avg(a.score), 1)                             AS avg_score,
       (array_agg(a.score ORDER BY s.created_at DESC))[1] AS last_score,
       max(s.created_at)                                  AS last_session_at
FROM session s
JOIN report r     ON r.session_id = s.id AND r.is_current
JOIN area_score a ON a.report_id = r.id
WHERE a.score IS NOT NULL
GROUP BY s.client_id, s.user_id, a.area_key;

COMMIT;

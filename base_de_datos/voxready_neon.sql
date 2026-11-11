-- VoxReady · CREAR TODO v2.1 (tablas + organización demo + escenarios de ejemplo)
-- Pegar completo en el SQL Editor de Neon y presionar Run.
-- OJO: borra y vuelve a crear las tablas de VoxReady (sirve aunque ya hayas corrido la v2.0).

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

-- =====================================================================
-- VoxReady · Datos iniciales (generado desde app.py v57 con base_de_datos/generar_sql.py)
-- Organización demo y los 3 escenarios de ejemplo (template_slug = slug).
-- Los usuarios se crean desde la página (registro), no aquí: ninguna contraseña queda en un archivo.
-- =====================================================================
BEGIN;

INSERT INTO client (id, name, retention_days, retention_mode) VALUES ('00000000-0000-4000-8000-000000000001', 'Organización demo', 30, 'full');

-- Escenario: Derrame químico en tres comunidades
WITH s AS (
  INSERT INTO scenario (client_id, slug, template_slug, title, icon, category, audience, difficulty, estimated_minutes,
                        summary, context, optics, optics_detail, interviewer_data, sort_order)
  VALUES ('00000000-0000-4000-8000-000000000001', 'derrame', 'derrame', 'Derrame químico en tres comunidades', '⚗️', 'health', 'Dirección',
          'hard', 8,
          'Eres el vocero tras un derrame que afectó el agua de tres comunidades. Entrevista de 8 preguntas.',
          'Industria química. Hace dos días hubo un derrame en la planta de tu empresa que contaminó el agua potable de tres comunidades (Villa Los Aromos, Las Canteras y El Esfuerzo). Hay familias sin agua de la llave, vecinos molestos y la autoridad sanitaria investiga. Eres el vocero de la empresa en una entrevista en vivo.',
          'empathetic', 'Cercana y humana: primero las personas afectadas, luego los datos técnicos.',
          '{"escena": "La empresa es responsable de un derrame químico en su planta que contaminó el agua potable de tres comunidades.", "afectados": "las familias de Villa Los Aromos", "consecuencia": "la gente sigue sin agua", "vocabulario": "derrame químico, tolueno, estanque, camiones aljibe, Villa Los Aromos, Las Canteras, El Esfuerzo, autoridad sanitaria"}'::jsonb, 0)
  RETURNING id)
INSERT INTO scenario_item (scenario_id, kind, text, sort_order)
SELECT s.id, v.kind, v.text, v.ord FROM s, (VALUES
    ('key_message', 'La prioridad es la salud de las familias y que vuelvan a tener agua segura.', 1),
    ('key_message', 'La empresa asume su responsabilidad y cubrirá los costos de los afectados.', 2),
    ('key_message', 'Informaremos con datos verificados y en plazos concretos.', 3),
    ('key_message', 'Trabajamos junto a la autoridad sanitaria.', 4),
    ('red_line', 'Especular sobre la causa antes del informe técnico.', 1),
    ('red_line', 'Minimizar el daño o culpar a los vecinos.', 2),
    ('red_line', 'Dar cifras o plazos que no estén confirmados.', 3),
    ('red_line', 'Negar o minimizar hechos que después se puedan comprobar.', 4),
    ('dossier', 'El derrame fue en la madrugada de hace dos días, cerca de las 4:40, en el estanque número 3 de la planta.', 1),
    ('dossier', 'Comunidades afectadas: Villa Los Aromos, Las Canteras y El Esfuerzo. Unas 2.300 familias sin agua de la llave; llega un solo camión aljibe al día y no alcanza.', 2),
    ('dossier', 'La empresa avisó a la autoridad sanitaria casi nueve horas después del derrame.', 3),
    ('dossier', 'La empresa no ha dicho públicamente qué sustancia fue. Tu fuente en el laboratorio municipal dice que es tolueno, un solvente industrial, y que la primera muestra salió seis veces sobre la norma.', 4),
    ('dossier', 'FILTRACIÓN: tienes un correo interno de hace tres semanas en que el jefe de mantención advierte una fisura en la válvula del estanque 3 y pide detenerlo; la reparación se postergó por costos.', 5),
    ('dossier', 'Caso humano: la señora Rosa Fuentes, 71 años, de Villa Los Aromos, terminó en urgencias con su nieto de 6 años por vómitos y dolor de estómago.', 6)
  ) AS v(kind, text, ord);

-- Escenario: Retiro de producto
WITH s AS (
  INSERT INTO scenario (client_id, slug, template_slug, title, icon, category, audience, difficulty, estimated_minutes,
                        summary, context, optics, optics_detail, interviewer_data, sort_order)
  VALUES ('00000000-0000-4000-8000-000000000001', 'retiro', 'retiro', 'Retiro de producto', '📦', 'reputational', 'Dirección',
          'intermediate', 6,
          'Eres el vocero ante la prensa tras detectar un defecto en un producto. Entrevista de 8 preguntas.',
          'Electrodomésticos. Tu empresa anunció hoy el retiro del hervidor eléctrico HX-200 porque el termostato puede fallar y sobrecalentarlo. El retiro es voluntario y se informó al SERNAC. Hay reportes de clientes con quemaduras leves y la prensa pregunta por qué no se avisó antes. Eres el vocero de la empresa en una entrevista en vivo.',
          'formal', 'Sobria e institucional: hechos, instrucciones claras y compromisos verificables.',
          '{"escena": "La empresa anunció hoy el retiro de su hervidor eléctrico HX-200 por una falla del termostato que puede sobrecalentarlo.", "afectados": "las familias que compraron el hervidor", "consecuencia": "hay hervidores peligrosos en miles de casas", "vocabulario": "retiro de producto, hervidor eléctrico, HX-200, termostato, sobrecalentamiento, lote, SERNAC, devolución"}'::jsonb, 1)
  RETURNING id)
INSERT INTO scenario_item (scenario_id, kind, text, sort_order)
SELECT s.id, v.kind, v.text, v.ord FROM s, (VALUES
    ('key_message', 'Quienes tengan un hervidor HX-200 deben desenchufarlo y dejar de usarlo hoy; el modelo está impreso en la base.', 1),
    ('key_message', 'La devolución es total o con cambio, en cualquier tienda y sin boleta.', 2),
    ('key_message', 'Hay un canal de atención 24/7 para resolver dudas y casos.', 3),
    ('key_message', 'Habrá una investigación con un informe público.', 4),
    ('red_line', 'Minimizar el riesgo («es muy poco probable», «es un caso aislado»).', 1),
    ('red_line', 'Culpar a los clientes por mal uso.', 2),
    ('red_line', 'Culpar al proveedor o especular sobre la causa antes de la investigación.', 3),
    ('red_line', 'Dar cifras de incidentes que no estén confirmadas.', 4),
    ('dossier', 'Se vendieron unas 48.000 unidades del HX-200 en Chile desde marzo.', 1),
    ('dossier', 'Los primeros reclamos por sobrecalentamiento llegaron hace tres semanas; el retiro se anunció recién hoy.', 2),
    ('dossier', 'FILTRACIÓN: un técnico de control de calidad te contó que en marzo detectaron temperaturas anormales en pruebas del lote 14 y se decidió seguir vendiendo.', 3),
    ('dossier', 'El SERNAC abrió una alerta de seguridad y pidió antecedentes a la empresa.', 4),
    ('dossier', 'Caso humano: una madre de Puente Alto cuenta que su hija de 8 años sufrió una quemadura leve en la mano cuando el hervidor se recalentó.', 5),
    ('dossier', 'En redes, clientes dicen que la tienda les exigió la boleta para devolverlo.', 6)
  ) AS v(kind, text, ord);

-- Escenario: Incidente de seguridad
WITH s AS (
  INSERT INTO scenario (client_id, slug, template_slug, title, icon, category, audience, difficulty, estimated_minutes,
                        summary, context, optics, optics_detail, interviewer_data, sort_order)
  VALUES ('00000000-0000-4000-8000-000000000001', 'datos', 'datos', 'Incidente de seguridad', '🔒', 'operational', 'Planta',
          'hard', 5,
          'Filtración de datos de clientes. Debes informar sin alarmar. Entrevista de 8 preguntas.',
          'Telecomunicaciones (internet y telefonía). Tu empresa confirmó que un ataque informático expuso datos personales de clientes: nombre, RUT, correo, teléfono y dirección. Hasta ahora la investigación no muestra que se filtraran claves, pero todavía se revisa si hubo datos de tarjetas. Hay clientes recibiendo llamadas sospechosas. Eres el vocero de la empresa en una entrevista en vivo.',
          'technical', 'Precisa y calmada: qué pasó, qué datos sí y cuáles no, y qué debe hacer cada cliente.',
          '{"escena": "La empresa de telecomunicaciones confirmó que un ataque informático expuso datos personales de sus clientes.", "afectados": "los clientes cuyos datos quedaron expuestos", "consecuencia": "los datos de miles de clientes siguen expuestos", "vocabulario": "filtración de datos, ataque informático, RUT, phishing, peritos, ciberseguridad, clientes, contraseña"}'::jsonb, 2)
  RETURNING id)
INSERT INTO scenario_item (scenario_id, kind, text, sort_order)
SELECT s.id, v.kind, v.text, v.ord FROM s, (VALUES
    ('key_message', 'Qué datos se expusieron según la investigación hasta hoy y qué se sigue revisando, sin afirmar más de lo confirmado.', 1),
    ('key_message', 'Qué deben hacer los clientes: no entregar claves por teléfono y desconfiar de llamadas o correos sospechosos.', 2),
    ('key_message', 'El incidente está contenido y trabajamos con peritos externos.', 3),
    ('key_message', 'Hay un canal de ayuda y se avisará a cada cliente afectado.', 4),
    ('red_line', 'Afirmar que «no hay ningún riesgo» o que «está todo bajo control» sin decir qué se hizo.', 1),
    ('red_line', 'Culpar al proveedor de tecnología.', 2),
    ('red_line', 'Dar detalles técnicos de la vulnerabilidad.', 3),
    ('red_line', 'Especular sobre quiénes son los atacantes.', 4),
    ('dossier', 'Son unos 210.000 clientes afectados.', 1),
    ('dossier', 'El ataque se detectó hace cuatro días; la empresa lo informó recién ayer.', 2),
    ('dossier', 'FILTRACIÓN: en un foro de internet se publicó una muestra que incluye los últimos cuatro dígitos de tarjetas, lo que contradice a la empresa.', 3),
    ('dossier', 'Un informe de auditoría del año pasado recomendó reforzar la seguridad del sistema afectado y no se implementó a tiempo.', 4),
    ('dossier', 'Un abogado experto en ciberseguridad te explicó que, con la Ley Marco de Ciberseguridad, una empresa de servicios esenciales como esta debe dar una alerta temprana a la Agencia Nacional de Ciberseguridad en un máximo de 3 horas desde que detecta el incidente.', 5),
    ('dossier', 'Caso humano: un cliente de 67 años recibió una llamada de alguien que conocía su RUT y su dirección y le pidió su clave.', 6)
  ) AS v(kind, text, ord);

COMMIT;

-- Comprobación: debe mostrar 11 tablas y 3 escenarios con sus ítems
SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE') AS tablas,
       (SELECT count(*) FROM scenario) AS escenarios,
       (SELECT count(*) FROM scenario_item) AS items;

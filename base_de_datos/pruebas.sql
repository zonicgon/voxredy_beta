-- =====================================================================
-- VoxReady · Pruebas del modelo v2.1 (correr DESPUÉS de voxready_neon.sql, en una base de prueba)
-- Cada prueba muestra OK o lanza un error. Todo se deshace al final (ROLLBACK): no deja datos.
-- =====================================================================
BEGIN;
DO $$
DECLARE
    cli uuid := '00000000-0000-4000-8000-000000000001';
    voc uuid; adm uuid; esc uuid; esc_propio uuid; ses uuid; ses2 uuid; rep uuid; n int; ok boolean;
BEGIN
    -- 1. Usuarios con rol de la página
    INSERT INTO app_user (client_id, email, display_name, first_name, last_name, password_hash, role)
    VALUES (cli, 'bryan.retamales@voxready.demo', 'Bryan Retamales', 'Bryan', 'Retamales', '$2b$12$hash', 'spokesperson') RETURNING id INTO voc;
    INSERT INTO app_user (client_id, email, display_name, first_name, last_name, password_hash, role)
    VALUES (cli, 'carlos.ruiz@voxready.demo', 'Carlos Ruiz', 'Carlos', 'Ruiz', '$2b$12$hash', 'client_admin') RETURNING id INTO adm;
    RAISE NOTICE 'OK 1: vocero y admin creados';

    -- 2. Correo único sin importar mayúsculas
    BEGIN
        INSERT INTO app_user (client_id, email, display_name, password_hash, role) VALUES (cli, 'BRYAN.Retamales@voxready.demo', 'x', 'h', 'spokesperson');
        RAISE EXCEPTION 'FALLA 2: aceptó un correo repetido';
    EXCEPTION WHEN unique_violation THEN RAISE NOTICE 'OK 2: correo repetido rechazado';
    END;

    -- 3. Escenarios de ejemplo actualizados (con 4+4 ítems y dossier)
    SELECT count(*) INTO n FROM scenario WHERE template_slug IS NOT NULL AND status = 'active';
    IF n <> 3 THEN RAISE EXCEPTION 'FALLA 3: % escenarios de ejemplo', n; END IF;
    SELECT count(*) INTO n FROM scenario_item i JOIN scenario s ON s.id = i.scenario_id WHERE s.slug = 'datos' AND i.kind = 'dossier';
    IF n < 6 THEN RAISE EXCEPTION 'FALLA 3: el dossier de «datos» no está al día (% datos)', n; END IF;
    PERFORM 1 FROM scenario_item WHERE text LIKE '%alertas internas previas%';
    IF FOUND THEN RAISE EXCEPTION 'FALLA 3: quedó la línea roja vieja que revelaba el dossier'; END IF;
    RAISE NOTICE 'OK 3: 3 escenarios de ejemplo al día';

    -- 4. Tema propio del admin, con retención propia; ejemplo editado
    INSERT INTO scenario (client_id, slug, title, category, audience, difficulty, estimated_minutes, summary, context, optics, retention_days, created_by)
    VALUES (cli, 'esc_trenes', 'Corte de trenes', 'operational', 'Planta', 'intermediate', 6, 'Resumen', 'Transporte. Una falla dejó sin trenes a 40 mil pasajeros.', 'empathetic', 90, adm)
    RETURNING id INTO esc_propio;
    INSERT INTO scenario_item (scenario_id, kind, text, sort_order) VALUES (esc_propio, 'key_message', 'Devolvemos el pasaje', 1), (esc_propio, 'red_line', 'Culpar a los pasajeros', 1);
    UPDATE scenario SET is_modified = true, title = 'Retiro HX-200 (editado)' WHERE slug = 'retiro';
    BEGIN
        UPDATE scenario SET is_modified = true WHERE id = esc_propio;
        RAISE EXCEPTION 'FALLA 4: un tema propio quedó marcado como «ejemplo modificado»';
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK 4: tema propio y ejemplo editado';
    END;

    -- 5. Sesión: no puede empezar sin consentimiento
    SELECT id INTO esc FROM scenario WHERE slug = 'derrame';
    BEGIN
        INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, started_at) VALUES (cli, voc, esc, 'Derrame', 'in_progress', now());
        RAISE EXCEPTION 'FALLA 5: empezó sin consentimiento';
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK 5: sin consentimiento no se puede empezar';
    END;

    -- 6. Sesión con consentimiento, guardada pregunta por pregunta, luego completa con informe y 4 áreas
    INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, consent_accepted, consent_text_version, consented_at,
                         started_at, interviewee, app_version, retention_mode, expires_at)
    VALUES (cli, voc, esc, 'Derrame químico en tres comunidades', 'in_progress', true, '2026-11-v56', now() - interval '1 minute',
            now(), '{"first_name":"Bryan","last_name":"Retamales"}', '56', 'full', now() + interval '30 days') RETURNING id INTO ses;
    UPDATE session SET transcript = transcript || '[{"role":"assistant","content":"¿Qué pasó?"},{"role":"user","content":"Hubo un derrame."}]'::jsonb WHERE id = ses;
    UPDATE session SET status = 'completed', finished_at = now() WHERE id = ses;
    INSERT INTO report (session_id, overall_score, judges, final_report, rubric_version, ai_provider) VALUES (ses, 2.4, '{}', '{"puntaje_global":2.4}', 'app-56', 'groq') RETURNING id INTO rep;
    INSERT INTO area_score (report_id, area_key, score) VALUES (rep, 'expression', 3.7), (rep, 'voice_tone', 2.3), (rep, 'coherence', 1.2), (rep, 'empathy', 1.9);
    SELECT count(*) INTO n FROM v_user_area_progress WHERE user_id = voc;
    IF n <> 4 THEN RAISE EXCEPTION 'FALLA 6: la vista de progreso muestra % áreas', n; END IF;
    RAISE NOTICE 'OK 6: sesión completa con informe y progreso por área';

    -- 7. Política «solo notas»: una sesión terminada no puede guardar transcripción
    UPDATE client SET retention_days = 90, retention_mode = 'scores_only' WHERE id = cli;
    INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, consent_accepted, consent_text_version, consented_at, started_at, retention_mode, expires_at, transcript)
    VALUES (cli, voc, esc_propio, 'Corte de trenes', 'in_progress', true, '2026-11-v56', now(), now(), 'scores_only', now() + interval '90 days',
            '[{"role":"user","content":"Devolvemos el pasaje."}]') RETURNING id INTO ses2;
    BEGIN
        UPDATE session SET status = 'completed' WHERE id = ses2;
        RAISE EXCEPTION 'FALLA 7: con «solo notas» quedó guardada la transcripción';
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK 7: «solo notas» exige vaciar la transcripción';
    END;
    UPDATE session SET status = 'completed', transcript = '[]' WHERE id = ses2;

    -- 8. Entrevista abandonada sin respuestas: la purga la borra; la vencida también
    INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, consent_accepted, consented_at) VALUES (cli, voc, esc, 'Derrame', 'abandoned', true, now());
    INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, consent_accepted, consented_at, expires_at) VALUES (cli, voc, esc, 'Derrame', 'completed', true, now(), now() - interval '1 day');
    n := purge_expired_sessions();
    IF n <> 2 THEN RAISE EXCEPTION 'FALLA 8: la purga borró % sesiones (esperaba 2)', n; END IF;
    RAISE NOTICE 'OK 8: purga de vencidas y abandonadas vacías';

    -- 9. Un tema con sesiones no se borra: se archiva
    BEGIN
        DELETE FROM scenario WHERE id = esc_propio;
        RAISE EXCEPTION 'FALLA 9: se borró un tema con sesiones';
    EXCEPTION WHEN foreign_key_violation THEN RAISE NOTICE 'OK 9: un tema con sesiones no se borra (se archiva)';
    END;
    UPDATE scenario SET status = 'archived' WHERE id = esc_propio;

    -- 10. Recuperar contraseña: máximo 5 intentos
    INSERT INTO password_reset (user_id, code_hash) VALUES (voc, 'hash-del-codigo');
    BEGIN
        UPDATE password_reset SET attempts = 6 WHERE user_id = voc;
        RAISE EXCEPTION 'FALLA 10: permitió más de 5 intentos';
    EXCEPTION WHEN check_violation THEN RAISE NOTICE 'OK 10: código con máximo 5 intentos';
    END;

    -- 11. Agenda de la próxima sesión
    UPDATE app_user SET next_session_date = DATE '2026-11-20' WHERE id = voc;
    RAISE NOTICE 'OK 11: agenda guardada en app_user';

    -- 12. Solicitud de borrado: una pendiente por usuario; al procesar se borra todo y queda constancia sin datos
    INSERT INTO deletion_request (client_id, user_id, user_label) VALUES (cli, voc, 'Bryan Retamales (bryan.retamales@voxready.demo)');
    BEGIN
        INSERT INTO deletion_request (client_id, user_id, user_label) VALUES (cli, voc, 'otra');
        RAISE EXCEPTION 'FALLA 12: dos solicitudes pendientes';
    EXCEPTION WHEN unique_violation THEN NULL;
    END;
    UPDATE deletion_request SET status = 'processed', processed_at = now(), processed_by = adm, user_label = NULL WHERE user_id = voc;
    DELETE FROM app_user WHERE id = voc;
    SELECT count(*) INTO n FROM session WHERE user_id = voc;
    IF n <> 0 THEN RAISE EXCEPTION 'FALLA 12: quedaron % sesiones del usuario borrado', n; END IF;
    SELECT (user_id IS NULL AND user_label IS NULL AND status = 'processed') INTO ok FROM deletion_request LIMIT 1;
    IF NOT ok THEN RAISE EXCEPTION 'FALLA 12: la constancia guarda datos personales'; END IF;
    SELECT count(*) INTO n FROM report;
    IF n <> 0 THEN RAISE EXCEPTION 'FALLA 12: quedaron % informes', n; END IF;
    RAISE NOTICE 'OK 12: borrado de cuenta con todo y constancia sin datos personales';

    RAISE NOTICE 'TODAS LAS PRUEBAS OK';
END $$;
ROLLBACK;

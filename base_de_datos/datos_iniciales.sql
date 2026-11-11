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

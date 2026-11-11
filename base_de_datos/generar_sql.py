"""Genera los datos iniciales de VoxReady desde TEMAS de app.py y arma el archivo único para Neon.

Uso (desde la carpeta del proyecto):  python base_de_datos/generar_sql.py
Crea:
  base_de_datos/datos_iniciales.sql   organización demo + los 3 escenarios de ejemplo
  base_de_datos/voxready_neon.sql     esquema.sql + datos_iniciales.sql (para pegar en el SQL Editor de Neon)
Vuelve a correrlo cada vez que cambien los temas en app.py, para que la base no quede desfasada.
"""
import json
import pathlib
import re
import sys

RAIZ = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import app  # noqa: E402

ID_CLIENTE = "00000000-0000-4000-8000-000000000001"
CATEGORIA = {"Sanitaria": "health", "Reputacional": "reputational", "Operativa": "operational"}
NIVEL = {"Básico": "basic", "Intermedio": "intermediate", "Difícil": "hard"}
OPTICA = {"Empática": "empathetic", "Formal": "formal", "Técnica": "technical"}


def q(texto):
    """Literal SQL seguro (comillas simples duplicadas)."""
    return "'" + str(texto).replace("'", "''") + "'"


def escenario(slug, t, orden):
    minutos = int(re.search(r"\d+", t["duracion"]).group())
    datos = {k: t[k] for k in ("escena", "afectados", "consecuencia", "vocabulario")}
    items = ([("key_message", x) for x in t["mensajes_clave"]] + [("red_line", x) for x in t["lineas_rojas"]]
             + [("dossier", x) for x in t["dossier"]])
    contador = {}
    filas = []
    for kind, texto in items:
        contador[kind] = contador.get(kind, 0) + 1
        filas.append(f"    ({q(kind)}, {q(texto)}, {contador[kind]})")
    return f"""-- Escenario: {t['titulo']}
WITH s AS (
  INSERT INTO scenario (client_id, slug, template_slug, title, icon, category, audience, difficulty, estimated_minutes,
                        summary, context, optics, optics_detail, interviewer_data, sort_order)
  VALUES ({q(ID_CLIENTE)}, {q(slug)}, {q(slug)}, {q(t['titulo'])}, {q(t['icono'])}, {q(CATEGORIA[t['categoria']])}, {q(t['publico'])},
          {q(NIVEL[t['nivel']])}, {minutos},
          {q(t['resumen'])},
          {q(t['contexto'])},
          {q(OPTICA[t['optica']])}, {q(t['optica_detalle'])},
          {q(json.dumps(datos, ensure_ascii=False))}::jsonb, {orden})
  RETURNING id)
INSERT INTO scenario_item (scenario_id, kind, text, sort_order)
SELECT s.id, v.kind, v.text, v.ord FROM s, (VALUES
{(","+chr(10)).join(filas)}
  ) AS v(kind, text, ord);
"""


def main():
    partes = [f"""-- =====================================================================
-- VoxReady · Datos iniciales (generado desde app.py v{app.VERSION} con base_de_datos/generar_sql.py)
-- Organización demo y los {len(app.TEMAS)} escenarios de ejemplo (template_slug = slug).
-- Los usuarios se crean desde la página (registro), no aquí: ninguna contraseña queda en un archivo.
-- =====================================================================
BEGIN;

INSERT INTO client (id, name, retention_days, retention_mode) VALUES ({q(ID_CLIENTE)}, 'Organización demo', 30, 'full');
"""]
    for i, (slug, t) in enumerate(app.TEMAS.items()):
        partes.append(escenario(slug, t, i))
    partes.append("COMMIT;\n")
    datos = "\n".join(partes)
    carpeta = RAIZ / "base_de_datos"
    (carpeta / "datos_iniciales.sql").write_text(datos, encoding="utf-8")
    esquema = (carpeta / "esquema.sql").read_text(encoding="utf-8")
    todo = ("-- VoxReady · CREAR TODO v2.1 (tablas + organización demo + escenarios de ejemplo)\n"
            "-- Pegar completo en el SQL Editor de Neon y presionar Run.\n"
            "-- OJO: borra y vuelve a crear las tablas de VoxReady (sirve aunque ya hayas corrido la v2.0).\n\n"
            + esquema + "\n" + datos +
            "\n-- Comprobación: debe mostrar 11 tablas y "
            f"{len(app.TEMAS)} escenarios con sus ítems\n"
            "SELECT (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE') AS tablas,\n"
            "       (SELECT count(*) FROM scenario) AS escenarios,\n"
            "       (SELECT count(*) FROM scenario_item) AS items;\n")
    (carpeta / "voxready_neon.sql").write_text(todo, encoding="utf-8")
    print(f"OK: {len(app.TEMAS)} escenarios · datos_iniciales.sql y voxready_neon.sql generados")


if __name__ == "__main__":
    main()

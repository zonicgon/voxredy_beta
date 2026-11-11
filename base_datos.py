"""VoxReady · conexión a la base de datos (PostgreSQL en Neon) — v58

Rutas /api/auth/* (cuentas), /api/escenarios*, /api/sesiones*, /api/agenda, /api/organizacion*,
/api/cuenta/borrado, /api/borrados*, /api/panel y /api/bd/estado.

Variables de entorno (en Vercel):
  DATABASE_URL   cadena de conexión de Neon (la crea la integración Vercel–Neon)
  JWT_SECRET     texto largo y aleatorio para firmar los tokens de sesión
  ACCESO_DEMO    "0" para apagar los botones de acceso rápido de la beta (por defecto, encendidos)
  RESEND_API_KEY y CORREO_REMITENTE   (opcional) para mandar el código de recuperación de contraseña

La página usa la base solo si DATABASE_URL y JWT_SECRET están configuradas; si no, sigue como maqueta
(todo en el navegador). Los nombres de la página (en español) se traducen aquí a los de la base (en inglés).
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

try:
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError:  # sin la librería, la página sigue como maqueta
    psycopg = None

router = APIRouter()
_CONFIG = {"temas": {}, "version": "", "limitar": None}

# ------------------------------------------------------------------ equivalencias página ↔ base
CATEGORIA = {"Sanitaria": "health", "Reputacional": "reputational", "Operativa": "operational"}
NIVEL = {"Básico": "basic", "Intermedio": "intermediate", "Difícil": "hard"}
OPTICA = {"Empática": "empathetic", "Formal": "formal", "Técnica": "technical"}
ROL = {"vocero": "spokesperson", "admin": "client_admin"}
AREA = {"Expresión": "expression", "Tono de voz": "voice_tone", "Coherencia": "coherence", "Empatía": "empathy"}
NIVEL_AREA = {"Prioridad alta": "high_priority", "Por reforzar": "reinforce", "Afinar detalles": "fine_tune"}
CONSERVAR = {"completo": "full", "solo_notas": "scores_only"}
_inv = lambda d: {v: k for k, v in d.items()}
CATEGORIA_ES, NIVEL_ES, OPTICA_ES, ROL_ES, CONSERVAR_ES = map(_inv, (CATEGORIA, NIVEL, OPTICA, ROL, CONSERVAR))
ROL_ES["master_config"] = "admin"
LIMITES = {"titulo": 80, "contexto": 1500, "item": 220, "dossier": 300, "max_items": 8, "max_dossier": 10}
DEMO = {
    "bryan.retamales@voxready.demo": ("Bryan", "Retamales", "spokesperson"),
    "carlos.ruiz@voxready.demo": ("Carlos", "Ruiz", "client_admin"),
}
ITERACIONES = 390_000  # PBKDF2-SHA256


def configurar(temas, version, limitar=None):
    """app.py entrega los temas de ejemplo (para «Restaurar el original»), su versión y el límite por IP."""
    _CONFIG.update(temas=temas, version=version, limitar=limitar)


def url_bd():
    return os.getenv("DATABASE_URL", "").strip()


def secreto():
    return os.getenv("JWT_SECRET", "").strip()


def disponible():
    return bool(psycopg and url_bd() and len(secreto()) >= 16)


def motivo_no_disponible():
    if psycopg is None:
        return "Falta la librería psycopg en requirements.txt."
    if not url_bd():
        return "Falta DATABASE_URL en las variables de entorno."
    if len(secreto()) < 16:
        return "Falta JWT_SECRET (al menos 16 caracteres) en las variables de entorno."
    return ""


@contextmanager
def conexion():
    if not disponible():
        raise HTTPException(status_code=503, detail="La base de datos no está configurada: " + motivo_no_disponible())
    try:
        # prepare_threshold=None: Neon usa un «pooler» que no admite sentencias preparadas
        con = psycopg.connect(url_bd(), row_factory=dict_row, connect_timeout=10, prepare_threshold=None)
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"No se pudo conectar a la base de datos ({type(e).__name__}).")
    try:
        with con:
            yield con
    finally:
        con.close()


_ESTADO_CACHE = {"t": 0, "v": None}


def estado(forzar=False):
    """Para /api/salud y el diagnóstico: ¿hay conexión? ¿cuántas tablas y escenarios?"""
    if not disponible():
        return {"configurada": False, "conectada": False, "detalle": motivo_no_disponible()}
    if not forzar and _ESTADO_CACHE["v"] and time.time() - _ESTADO_CACHE["t"] < 60:
        return _ESTADO_CACHE["v"]
    t0 = time.time()
    try:
        with conexion() as c:
            tablas = c.execute("SELECT count(*) AS n FROM information_schema.tables WHERE table_schema = 'public' "
                               "AND table_type = 'BASE TABLE'").fetchone()["n"]
            escenarios = c.execute("SELECT count(*) AS n FROM scenario WHERE status <> 'archived'").fetchone()["n"]
            usuarios = c.execute("SELECT count(*) AS n FROM app_user").fetchone()["n"]
        v = {"configurada": True, "conectada": True, "tablas": tablas, "escenarios": escenarios, "usuarios": usuarios,
             "ms": round((time.time() - t0) * 1000)}
    except HTTPException as e:
        v = {"configurada": True, "conectada": False, "detalle": e.detail}
    except Exception as e:  # tablas que no existen, permisos…
        v = {"configurada": True, "conectada": False, "detalle": f"Conecta, pero falla la consulta ({type(e).__name__}): ¿corriste voxready_neon.sql?"}
    _ESTADO_CACHE.update(t=time.time(), v=v)
    return v


# ------------------------------------------------------------------ contraseñas y tokens
def cifrar(contrasena):
    sal = secrets.token_bytes(16)
    h = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), sal, ITERACIONES)
    return f"pbkdf2_sha256${ITERACIONES}${base64.b64encode(sal).decode()}${base64.b64encode(h).decode()}"


def verificar(contrasena, guardado):
    try:
        alg, it, sal, h = guardado.split("$")
        if alg != "pbkdf2_sha256":
            return False
        calc = hashlib.pbkdf2_hmac("sha256", contrasena.encode("utf-8"), base64.b64decode(sal), int(it))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def crear_token(u):
    cab = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    ahora = int(time.time())
    cuerpo = _b64(json.dumps({"sub": str(u["id"]), "cli": str(u["client_id"]), "rol": u["role"],
                              "iat": ahora, "exp": ahora + 7 * 24 * 3600}).encode())
    firma = _b64(hmac.new(secreto().encode(), f"{cab}.{cuerpo}".encode(), hashlib.sha256).digest())
    return f"{cab}.{cuerpo}.{firma}"


def leer_token(token):
    try:
        cab, cuerpo, firma = token.split(".")
        esperado = _b64(hmac.new(secreto().encode(), f"{cab}.{cuerpo}".encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(esperado, firma):
            return None
        datos = json.loads(_b64d(cuerpo))
        return datos if datos.get("exp", 0) > time.time() else None
    except Exception:
        return None


def _token_de(authorization):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return ""


def usuario_de_token(c, authorization):
    datos = leer_token(_token_de(authorization)) if authorization else None
    if not datos:
        return None
    return c.execute("SELECT * FROM app_user WHERE id = %s AND status = 'active'", (datos["sub"],)).fetchone()


def _limitar(request: Request):
    if _CONFIG["limitar"]:
        _CONFIG["limitar"](request)


def usuario_actual(request: Request, authorization: Optional[str] = Header(default=None)):
    _limitar(request)
    with conexion() as c:
        u = usuario_de_token(c, authorization)
    if not u:
        raise HTTPException(status_code=401, detail="Tu sesión venció. Vuelve a iniciar sesión.")
    return u


def admin_actual(u=Depends(usuario_actual)):
    if u["role"] not in ("client_admin", "master_config"):
        raise HTTPException(status_code=403, detail="Solo el administrador del cliente puede hacer esto.")
    return u


def ms(t):
    if t is None:
        return None
    if isinstance(t, date) and not isinstance(t, datetime):
        t = datetime(t.year, t.month, t.day, tzinfo=timezone.utc)
    return int(t.timestamp() * 1000)


def publico(u):
    return {"id": str(u["id"]), "organization_id": str(u["client_id"]) if u["client_id"] else None,
            "nombre": u["first_name"] or u["display_name"], "apellido": u["last_name"] or "",
            "correo": u["email"], "rol": ROL_ES.get(u["role"], "vocero"),
            "demo": u["email"].endswith("@voxready.demo"), "creado": ms(u["created_at"])}


def limpio(t, n):
    return re.sub(r"\s+", " ", str(t or "")).strip()[:n]


def correo_valido(c):
    return re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", c or "") is not None


def cliente_registro(c):
    """En la beta, las cuentas nuevas entran a la organización demo (la primera creada)."""
    r = c.execute("SELECT id FROM client WHERE status = 'active' ORDER BY created_at, id LIMIT 1").fetchone()
    if not r:
        raise HTTPException(status_code=503, detail="La base no tiene ninguna organización: corre voxready_neon.sql.")
    return r["id"]


# ------------------------------------------------------------------ estado (público, sin conexión)
@router.get("/api/bd/estado")
def bd_estado():
    """La página decide aquí si usa la base o sigue como maqueta. No conecta: solo mira la configuración."""
    return {"disponible": disponible(), "detalle": motivo_no_disponible(),
            "acceso_demo": os.getenv("ACCESO_DEMO", "1").strip() not in ("0", "no", "false"),
            "correo_recuperacion": bool(os.getenv("RESEND_API_KEY") and os.getenv("CORREO_REMITENTE"))}


# ------------------------------------------------------------------ cuentas
class Registro(BaseModel):
    nombre: str = Field(max_length=60)
    apellido: str = Field(max_length=60)
    correo: str = Field(max_length=120)
    contrasena: str = Field(min_length=6, max_length=200)


class Entrada(BaseModel):
    correo: str = Field(max_length=120)
    contrasena: str = Field(max_length=200)


class Demo(BaseModel):
    correo: str = Field(max_length=120)


class PedirCodigo(BaseModel):
    correo: str = Field(max_length=120)


class Restablecer(BaseModel):
    correo: str = Field(max_length=120)
    codigo: str = Field(max_length=10)
    nueva: str = Field(min_length=6, max_length=200)


def _respuesta_sesion(c, u):
    c.execute("UPDATE app_user SET last_login_at = now() WHERE id = %s", (u["id"],))
    return {"token": crear_token(u), "usuario": publico(u)}


@router.post("/api/auth/registrar")
def registrar(d: Registro, request: Request):
    _limitar(request)
    nombre, apellido, correo = limpio(d.nombre, 60), limpio(d.apellido, 60), limpio(d.correo, 120).lower()
    if not nombre or not apellido:
        raise HTTPException(status_code=400, detail="Escribe tu nombre y tu apellido.")
    if not correo_valido(correo):
        raise HTTPException(status_code=400, detail="Escribe un correo válido.")
    with conexion() as c:
        if c.execute("SELECT 1 FROM app_user WHERE lower(email) = %s", (correo,)).fetchone():
            raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo. Inicia sesión.")
        u = c.execute("""INSERT INTO app_user (client_id, email, display_name, first_name, last_name, password_hash, role)
                         VALUES (%s, %s, %s, %s, %s, %s, 'spokesperson') RETURNING *""",
                      (cliente_registro(c), correo, f"{nombre} {apellido}", nombre, apellido, cifrar(d.contrasena))).fetchone()
        return _respuesta_sesion(c, u)


@router.post("/api/auth/entrar")
def entrar(d: Entrada, request: Request):
    _limitar(request)
    correo = limpio(d.correo, 120).lower()
    with conexion() as c:
        u = c.execute("SELECT * FROM app_user WHERE lower(email) = %s AND status = 'active'", (correo,)).fetchone()
        if not u and correo in DEMO and d.contrasena == "voxready" and bd_estado()["acceso_demo"]:
            u = _crear_demo(c, correo)
        if not u or not verificar(d.contrasena, u["password_hash"]):
            raise HTTPException(status_code=401, detail="Correo o contraseña incorrectos.")
        return _respuesta_sesion(c, u)


def _crear_demo(c, correo):
    nombre, apellido, rol = DEMO[correo]
    return c.execute("""INSERT INTO app_user (client_id, email, display_name, first_name, last_name, password_hash, role)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT ((lower(email))) DO UPDATE SET status = app_user.status RETURNING *""",
                     (cliente_registro(c), correo, f"{nombre} {apellido}", nombre, apellido, cifrar("voxready"), rol)).fetchone()


@router.post("/api/auth/demo")
def entrar_demo(d: Demo, request: Request):
    """Botones de «Acceso rápido» de la beta. Se apagan con ACCESO_DEMO=0."""
    _limitar(request)
    correo = limpio(d.correo, 120).lower()
    if correo not in DEMO or not bd_estado()["acceso_demo"]:
        raise HTTPException(status_code=403, detail="El acceso rápido está desactivado.")
    with conexion() as c:
        u = c.execute("SELECT * FROM app_user WHERE lower(email) = %s", (correo,)).fetchone() or _crear_demo(c, correo)
        if u["status"] != "active":
            raise HTTPException(status_code=403, detail="Esa cuenta está desactivada.")
        return _respuesta_sesion(c, u)


@router.get("/api/auth/yo")
def yo(u=Depends(usuario_actual)):
    return {"usuario": publico(u)}


def _enviar_correo(destino, codigo):
    clave, remitente = os.getenv("RESEND_API_KEY", "").strip(), os.getenv("CORREO_REMITENTE", "").strip()
    if not (clave and remitente):
        return False
    cuerpo = json.dumps({"from": remitente, "to": [destino], "subject": "Tu código de VoxReady",
                         "text": f"Tu código para cambiar la contraseña de VoxReady es {codigo}. Vence en 15 minutos. "
                                 "Si no lo pediste, ignora este correo."}).encode()
    req = urllib.request.Request("https://api.resend.com/emails", data=cuerpo, method="POST",
                                 headers={"Authorization": f"Bearer {clave}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, OSError):
        return False


def _hash_codigo(codigo):
    return hmac.new(secreto().encode(), codigo.encode(), hashlib.sha256).hexdigest()


@router.post("/api/auth/codigo")
def pedir_codigo(d: PedirCodigo, request: Request):
    _limitar(request)
    if not bd_estado()["correo_recuperacion"]:
        raise HTTPException(status_code=503, detail="La recuperación por correo todavía no está configurada en el servidor. "
                                                    "Pídele a quien administra VoxReady que te ayude a entrar.")
    correo = limpio(d.correo, 120).lower()
    if not correo_valido(correo):
        raise HTTPException(status_code=400, detail="Escribe un correo válido.")
    with conexion() as c:
        u = c.execute("SELECT id FROM app_user WHERE lower(email) = %s AND status = 'active'", (correo,)).fetchone()
        if u:
            codigo = f"{secrets.randbelow(900000) + 100000}"
            c.execute("DELETE FROM password_reset WHERE user_id = %s", (u["id"],))
            c.execute("INSERT INTO password_reset (user_id, code_hash) VALUES (%s, %s)", (u["id"], _hash_codigo(codigo)))
            if not _enviar_correo(correo, codigo):
                raise HTTPException(status_code=502, detail="No se pudo enviar el correo. Prueba de nuevo en unos minutos.")
    return {"codigo": None}  # no se revela si la cuenta existe


@router.post("/api/auth/restablecer")
def restablecer(d: Restablecer, request: Request):
    _limitar(request)
    correo = limpio(d.correo, 120).lower()
    with conexion() as c:
        r = c.execute("""SELECT p.*, u.id AS uid FROM password_reset p JOIN app_user u ON u.id = p.user_id
                         WHERE lower(u.email) = %s AND p.expires_at > now() AND p.used_at IS NULL
                         ORDER BY p.created_at DESC LIMIT 1""", (correo,)).fetchone()
        if not r:
            raise HTTPException(status_code=400, detail="El código venció o no existe. Pide uno nuevo.")
        if r["attempts"] >= 5:
            raise HTTPException(status_code=429, detail="Demasiados intentos. Pide un código nuevo.")
        if not hmac.compare_digest(r["code_hash"], _hash_codigo(d.codigo.strip())):
            c.execute("UPDATE password_reset SET attempts = attempts + 1 WHERE id = %s", (r["id"],))
            c.commit()
            raise HTTPException(status_code=400, detail="El código no es correcto.")
        c.execute("UPDATE app_user SET password_hash = %s WHERE id = %s", (cifrar(d.nueva), r["uid"]))
        c.execute("UPDATE password_reset SET used_at = now() WHERE id = %s", (r["id"],))
    return {"ok": True}


# ------------------------------------------------------------------ escenarios
SQL_ESCENARIOS = """
SELECT s.*, cl.retention_days AS ret_cliente,
       coalesce(json_agg(json_build_object('kind', i.kind, 'text', i.text) ORDER BY i.kind, i.sort_order)
                FILTER (WHERE i.id IS NOT NULL), '[]') AS items
FROM scenario s JOIN client cl ON cl.id = s.client_id
LEFT JOIN scenario_item i ON i.scenario_id = s.id
WHERE s.client_id = %s AND s.status <> 'archived' {extra}
GROUP BY s.id, cl.retention_days
ORDER BY s.sort_order, s.created_at
"""


def escenario_pagina(r, con_dossier):
    items = r["items"] if isinstance(r["items"], list) else json.loads(r["items"] or "[]")
    idata = r["interviewer_data"] or {}
    de = lambda k: [x["text"] for x in items if x["kind"] == k]
    return {
        "id": r["slug"], "uuid": str(r["id"]), "base_id": r["template_slug"], "modificado": r["is_modified"],
        "activo": r["status"] == "active", "titulo": r["title"], "icono": r["icon"] or "📰",
        "categoria": CATEGORIA_ES.get(r["category"], "Reputacional"), "publico": r["audience"],
        "nivel": NIVEL_ES.get(r["difficulty"], "Intermedio"), "duracion": f"≈ {r['estimated_minutes']} min",
        "resumen": r["summary"], "contexto": r["context"], "optica": OPTICA_ES.get(r["optics"], "Formal"),
        "optica_detalle": r["optics_detail"] or "", "afectados": idata.get("afectados", ""),
        "consecuencia": idata.get("consecuencia", ""), "retencion_dias": r["retention_days"],
        "retencion_efectiva": r["retention_days"] or r["ret_cliente"], "idiomas": r["languages"],
        "orden": r["sort_order"], "creado": ms(r["created_at"]),
        "mensajes_clave": de("key_message"), "lineas_rojas": de("red_line"),
        "dossier": de("dossier") if con_dossier else [],
    }


def _escenarios(c, client_id, incluir_inactivos=False):
    extra = "" if incluir_inactivos else "AND s.status = 'active'"
    return c.execute(SQL_ESCENARIOS.format(extra=extra), (client_id,)).fetchall()


def _escenario_por_slug(c, client_id, slug):
    filas = c.execute(SQL_ESCENARIOS.format(extra="AND s.slug = %s"), (client_id, slug)).fetchall()
    return filas[0] if filas else None


@router.get("/api/escenarios")
def listar_escenarios(inactivos: int = 0, u=Depends(usuario_actual)):
    es_admin = u["role"] != "spokesperson"
    with conexion() as c:
        filas = _escenarios(c, u["client_id"], bool(inactivos) and es_admin)
    return {"escenarios": [escenario_pagina(r, es_admin) for r in filas]}


def definicion_para_entrevista(authorization, slug):
    """Para /api/entrevistar y /api/evaluar: el tema completo (con lo que sabe el periodista) sale de la base,
    con el token del usuario. Así el dossier nunca pasa por la página. Devuelve None si no aplica."""
    if not (disponible() and authorization and slug):
        return None
    try:
        with conexion() as c:
            u = usuario_de_token(c, authorization)
            if not u:
                return None
            r = _escenario_por_slug(c, u["client_id"], slug)
    except HTTPException:
        return None
    if not r:
        return None
    e = escenario_pagina(r, True)
    idata = r["interviewer_data"] or {}
    return {"base_id": r["template_slug"], "titulo": e["titulo"], "contexto": e["contexto"], "escena": idata.get("escena", ""),
            "optica": e["optica"], "optica_detalle": e["optica_detalle"], "publico": e["publico"],
            "mensajes_clave": e["mensajes_clave"], "lineas_rojas": e["lineas_rojas"], "dossier": e["dossier"],
            "afectados": e["afectados"], "consecuencia": e["consecuencia"], "vocabulario": idata.get("vocabulario", "")}


class EscenarioIn(BaseModel):
    id: Optional[str] = Field(default=None, max_length=40)
    titulo: str = Field(default="", max_length=200)
    contexto: str = Field(default="", max_length=3000)
    optica: str = Field(default="Formal", max_length=20)
    publico: str = Field(default="Dirección", max_length=20)
    categoria: str = Field(default="Reputacional", max_length=20)
    nivel: str = Field(default="Intermedio", max_length=20)
    icono: str = Field(default="📰", max_length=10)
    retencion_dias: Optional[int] = None
    mensajes_clave: list[str] = Field(default_factory=list, max_length=20)
    lineas_rojas: list[str] = Field(default_factory=list, max_length=20)
    dossier: list[str] = Field(default_factory=list, max_length=20)
    afectados: str = Field(default="", max_length=200)


def validar_escenario(d: EscenarioIn):
    lista = lambda xs, n: [limpio(x, n) for x in xs if limpio(x, n)]
    v = {
        "titulo": limpio(d.titulo, LIMITES["titulo"]), "contexto": limpio(d.contexto, LIMITES["contexto"]),
        "optica": OPTICA.get(d.optica, "formal"), "publico": d.publico if d.publico in ("Dirección", "Planta", "Técnicos") else "Dirección",
        "categoria": CATEGORIA.get(d.categoria, "reputational"), "nivel": NIVEL.get(d.nivel, "intermediate"),
        "icono": limpio(d.icono, 4) or "📰", "retencion_dias": d.retencion_dias if d.retencion_dias in (30, 90, 180) else None,
        "mensajes_clave": lista(d.mensajes_clave, LIMITES["item"]), "lineas_rojas": lista(d.lineas_rojas, LIMITES["item"]),
        "dossier": lista(d.dossier, LIMITES["dossier"]), "afectados": limpio(d.afectados, 120),
    }
    if len(v["titulo"]) < 3:
        raise HTTPException(status_code=400, detail="Escribe el nombre del tema.")
    if len(v["contexto"]) < 30:
        raise HTTPException(status_code=400, detail="Escribe el contexto del escenario (al menos una o dos frases).")
    if not v["mensajes_clave"]:
        raise HTTPException(status_code=400, detail="Agrega al menos un mensaje clave.")
    if not v["lineas_rojas"]:
        raise HTTPException(status_code=400, detail="Agrega al menos una línea roja.")
    if len(v["mensajes_clave"]) > LIMITES["max_items"] or len(v["lineas_rojas"]) > LIMITES["max_items"]:
        raise HTTPException(status_code=400, detail=f"Máximo {LIMITES['max_items']} mensajes clave y {LIMITES['max_items']} líneas rojas.")
    if len(v["dossier"]) > LIMITES["max_dossier"]:
        raise HTTPException(status_code=400, detail=f"Máximo {LIMITES['max_dossier']} datos del periodista.")
    frases = re.split(r"(?<=[.!?])\s+", v["contexto"])
    v["resumen"] = limpio(" ".join(frases[:2]), 160) + " Entrevista de 8 preguntas."
    return v


def _poner_items(c, scenario_id, v):
    c.execute("DELETE FROM scenario_item WHERE scenario_id = %s", (scenario_id,))
    filas = [(scenario_id, k, t, i + 1) for k, clave in (("key_message", "mensajes_clave"), ("red_line", "lineas_rojas"), ("dossier", "dossier"))
             for i, t in enumerate(v[clave])]
    if filas:
        c.cursor().executemany("INSERT INTO scenario_item (scenario_id, kind, text, sort_order) VALUES (%s, %s, %s, %s)", filas)


@router.post("/api/escenarios")
def guardar_escenario(d: EscenarioIn, u=Depends(admin_actual)):
    v = validar_escenario(d)
    with conexion() as c:
        fila = d.id and c.execute("SELECT * FROM scenario WHERE client_id = %s AND slug = %s AND status <> 'archived'",
                                  (u["client_id"], d.id)).fetchone()
        if fila:
            idata = dict(fila["interviewer_data"] or {})
            idata["afectados"] = v["afectados"]
            # en un tema de ejemplo, el resumen de la tarjeta se mantiene salvo que cambie el contexto
            resumen = fila["summary"] if (fila["template_slug"] and fila["context"] == v["contexto"]) else v["resumen"]
            fila = c.execute("""UPDATE scenario SET title = %s, context = %s, optics = %s, audience = %s, category = %s, difficulty = %s,
                                icon = %s, retention_days = %s, summary = %s, interviewer_data = %s,
                                is_modified = (template_slug IS NOT NULL) WHERE id = %s RETURNING id""",
                             (v["titulo"], v["contexto"], v["optica"], v["publico"], v["categoria"], v["nivel"], v["icono"],
                              v["retencion_dias"], resumen, Jsonb(idata), fila["id"])).fetchone()
        else:
            fila = c.execute("""INSERT INTO scenario (client_id, slug, title, icon, category, audience, difficulty, estimated_minutes,
                                summary, context, optics, interviewer_data, retention_days, sort_order, created_by)
                                VALUES (%s, %s, %s, %s, %s, %s, %s, 6, %s, %s, %s, %s, %s,
                                        50 + (SELECT count(*) FROM scenario WHERE client_id = %s), %s) RETURNING id""",
                             (u["client_id"], "esc_" + secrets.token_hex(4), v["titulo"], v["icono"], v["categoria"], v["publico"],
                              v["nivel"], v["resumen"], v["contexto"], v["optica"],
                              Jsonb({"afectados": v["afectados"], "consecuencia": "", "escena": "", "vocabulario": ""}),
                              v["retencion_dias"], u["client_id"], u["id"])).fetchone()
        _poner_items(c, fila["id"], v)
        r = c.execute(SQL_ESCENARIOS.format(extra="AND s.id = %s"), (u["client_id"], fila["id"])).fetchone()
        return {"escenario": escenario_pagina(r, True)}


def _quedan_activos(c, client_id, excepto):
    return c.execute("SELECT count(*) AS n FROM scenario WHERE client_id = %s AND status = 'active' AND slug <> %s",
                     (client_id, excepto)).fetchone()["n"]


class Activo(BaseModel):
    activo: bool


@router.post("/api/escenarios/{slug}/activo")
def activar_escenario(slug: str, d: Activo, u=Depends(admin_actual)):
    with conexion() as c:
        if not d.activo and _quedan_activos(c, u["client_id"], slug) < 1:
            raise HTTPException(status_code=400, detail="Debe quedar al menos un tema activo para los voceros.")
        n = c.execute("UPDATE scenario SET status = %s WHERE client_id = %s AND slug = %s AND status <> 'archived'",
                      ("active" if d.activo else "inactive", u["client_id"], slug)).rowcount
    if not n:
        raise HTTPException(status_code=404, detail="Ese tema no existe.")
    return {"ok": True}


@router.delete("/api/escenarios/{slug}")
def archivar_escenario(slug: str, u=Depends(admin_actual)):
    """«Eliminar» = archivar: sus entrevistas lo siguen referenciando."""
    with conexion() as c:
        fila = c.execute("SELECT * FROM scenario WHERE client_id = %s AND slug = %s AND status <> 'archived'", (u["client_id"], slug)).fetchone()
        if not fila:
            raise HTTPException(status_code=404, detail="Ese tema no existe.")
        if fila["template_slug"]:
            raise HTTPException(status_code=400, detail="Los temas de ejemplo de VoxReady no se borran: puedes desactivarlos o restaurarlos.")
        if fila["status"] == "active" and _quedan_activos(c, u["client_id"], slug) < 1:
            raise HTTPException(status_code=400, detail="Debe quedar al menos un tema activo para los voceros.")
        c.execute("UPDATE scenario SET status = 'archived' WHERE id = %s", (fila["id"],))
    return {"ok": True}


@router.post("/api/escenarios/{slug}/restaurar")
def restaurar_escenario(slug: str, u=Depends(admin_actual)):
    with conexion() as c:
        fila = c.execute("SELECT * FROM scenario WHERE client_id = %s AND slug = %s", (u["client_id"], slug)).fetchone()
        t = fila and _CONFIG["temas"].get(fila["template_slug"] or "")
        if not t:
            raise HTTPException(status_code=404, detail="No se encontró el tema original.")
        minutos = int(re.search(r"\d+", t["duracion"]).group())
        c.execute("""UPDATE scenario SET title = %s, icon = %s, category = %s, audience = %s, difficulty = %s, estimated_minutes = %s,
                     summary = %s, context = %s, optics = %s, optics_detail = %s, interviewer_data = %s, retention_days = NULL,
                     is_modified = false WHERE id = %s""",
                  (t["titulo"], t["icono"], CATEGORIA[t["categoria"]], t["publico"], NIVEL[t["nivel"]], minutos, t["resumen"],
                   t["contexto"], OPTICA[t["optica"]], t["optica_detalle"],
                   Jsonb({k: t[k] for k in ("escena", "afectados", "consecuencia", "vocabulario")}), fila["id"]))
        _poner_items(c, fila["id"], {"mensajes_clave": t["mensajes_clave"], "lineas_rojas": t["lineas_rojas"], "dossier": t["dossier"]})
        r = c.execute(SQL_ESCENARIOS.format(extra="AND s.id = %s"), (u["client_id"], fila["id"])).fetchone()
        return {"escenario": escenario_pagina(r, True)}


# ------------------------------------------------------------------ sesiones (entrevistas)
class Consentimiento(BaseModel):
    aceptado_en: Optional[float] = None
    version: Optional[str] = Field(default=None, max_length=20)


class SesionIn(BaseModel):
    scenario_id: str = Field(max_length=40)
    scenario_titulo: str = Field(default="", max_length=150)
    nombre: str = Field(default="", max_length=60)
    apellido: str = Field(default="", max_length=60)
    version_app: str = Field(default="", max_length=10)
    consentimiento: Optional[Consentimiento] = None


class Mensaje(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(max_length=6000)


class Avance(BaseModel):
    transcripcion: list[Mensaje] = Field(default_factory=list, max_length=60)


class Completar(BaseModel):
    transcripcion: list[Mensaje] = Field(default_factory=list, max_length=60)
    informes: dict = Field(default_factory=dict)
    final: Optional[dict] = None


def _transcripcion(xs):
    return Jsonb([{"role": m.role, "content": m.content} for m in xs])


def _sesion_propia(c, u, sid):
    try:
        s = c.execute("SELECT * FROM session WHERE id = %s AND user_id = %s", (sid, u["id"])).fetchone()
    except Exception:
        s = None
    if not s:
        raise HTTPException(status_code=404, detail="Esa entrevista no existe.")
    return s


@router.post("/api/sesiones")
def crear_sesion(d: SesionIn, u=Depends(usuario_actual)):
    with conexion() as c:
        esc = c.execute("""SELECT s.id, s.title, coalesce(s.retention_days, cl.retention_days) AS dias, cl.retention_mode
                           FROM scenario s JOIN client cl ON cl.id = s.client_id
                           WHERE s.client_id = %s AND s.slug = %s AND s.status <> 'archived'""", (u["client_id"], d.scenario_id)).fetchone()
        if not esc:
            raise HTTPException(status_code=404, detail="Ese escenario ya no está disponible.")
        con_consent = bool(d.consentimiento and d.consentimiento.aceptado_en)
        ahora = datetime.now(timezone.utc)
        # la hora del consentimiento viene del navegador: nunca puede quedar después del inicio
        consentido = min(datetime.fromtimestamp(d.consentimiento.aceptado_en / 1000, tz=timezone.utc), ahora) if con_consent else None
        s = c.execute("""INSERT INTO session (client_id, user_id, scenario_id, scenario_title, status, consent_accepted, consent_text_version,
                         consented_at, started_at, interviewee, app_version, retention_mode, expires_at)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now() + make_interval(days => %s))
                         RETURNING id""",
                      (u["client_id"], u["id"], esc["id"], limpio(d.scenario_titulo, 150) or esc["title"],
                       "in_progress" if con_consent else "created", con_consent,
                       d.consentimiento.version if con_consent else None, consentido,
                       ahora if con_consent else None,
                       Jsonb({"first_name": limpio(d.nombre, 60) or u["first_name"], "last_name": limpio(d.apellido, 60) or u["last_name"]}),
                       limpio(d.version_app, 10), esc["retention_mode"], esc["dias"])).fetchone()
    return {"id": str(s["id"])}


@router.put("/api/sesiones/{sid}/avance")
def guardar_avance(sid: str, d: Avance, u=Depends(usuario_actual)):
    with conexion() as c:
        _sesion_propia(c, u, sid)
        c.execute("UPDATE session SET transcript = %s WHERE id = %s AND status IN ('created', 'in_progress')", (_transcripcion(d.transcripcion), sid))
    return {"ok": True}


@router.post("/api/sesiones/{sid}/incompleta")
def marcar_incompleta(sid: str, u=Depends(usuario_actual)):
    with conexion() as c:
        s = _sesion_propia(c, u, sid)
        if s["status"] in ("completed", "abandoned"):
            return {"ok": True}
        if not any(m.get("role") == "user" for m in (s["transcript"] or [])):
            c.execute("DELETE FROM session WHERE id = %s", (sid,))  # sin respuestas: no vale la pena guardarla
        elif s["consent_accepted"]:
            c.execute("UPDATE session SET status = 'abandoned', finished_at = now() WHERE id = %s", (sid,))
        else:
            c.execute("UPDATE session SET status = 'failed', finished_at = now() WHERE id = %s", (sid,))
    return {"ok": True}


def _solo_notas(informes):
    salida = {}
    for k, v in (informes or {}).items():
        if isinstance(v, dict) and isinstance(v.get("areas"), list):
            salida[k] = {"areas": [{"nombre": a.get("nombre"), "puntaje": a.get("puntaje")} for a in v["areas"] if isinstance(a, dict)],
                         **{c: v.get(c) for c in ("puntaje_global", "puntaje_global_voz", "puntaje_global_cara") if c in v}}
    return salida


def _nota(x):
    try:
        return max(0.0, min(5.0, round(float(x), 1)))
    except (TypeError, ValueError):
        return None


@router.post("/api/sesiones/{sid}/completar")
def completar_sesion(sid: str, d: Completar, u=Depends(usuario_actual)):
    with conexion() as c:
        s = _sesion_propia(c, u, sid)
        if not s["consent_accepted"]:
            raise HTTPException(status_code=400, detail="Esta entrevista no tiene consentimiento registrado: no se puede guardar.")
        solo_notas = s["retention_mode"] == "scores_only"
        c.execute("UPDATE session SET status = 'completed', finished_at = coalesce(finished_at, now()), transcript = %s WHERE id = %s",
                  (Jsonb([]) if solo_notas else _transcripcion(d.transcripcion), sid))
        final = d.final or {}
        jueces = _solo_notas(d.informes) if solo_notas else (d.informes or {})
        proveedor = next((str(v.get("proveedor"))[:30] for v in (d.informes or {}).values() if isinstance(v, dict) and v.get("proveedor")), "desconocido")
        version = (c.execute("SELECT coalesce(max(version_no), 0) + 1 AS n FROM report WHERE session_id = %s", (sid,)).fetchone()["n"])
        c.execute("UPDATE report SET is_current = false WHERE session_id = %s", (sid,))
        rep = c.execute("""INSERT INTO report (session_id, version_no, overall_score, summary, main_strength, main_recommendation,
                           judges, caps_applied, sources_available, final_report, rubric_version, ai_provider)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (sid, version, _nota(final.get("puntaje_global")), final.get("resumen"), final.get("fortaleza_principal"),
                         final.get("recomendacion_principal"), Jsonb(jueces), Jsonb(final.get("topes_aplicados") or []),
                         [k for k, v in (d.informes or {}).items() if v], Jsonb(final),
                         f"app-{_CONFIG['version']}", proveedor)).fetchone()
        trabajar = {a.get("nombre"): a for a in (final.get("areas_a_trabajar") or []) if isinstance(a, dict)}
        for a in final.get("areas") or []:
            clave = AREA.get(a.get("nombre")) if isinstance(a, dict) else None
            if not clave:
                continue
            t = trabajar.get(a["nombre"], {})
            c.execute("""INSERT INTO area_score (report_id, area_key, score, sources, comment, to_work_on, level, exercise)
                         VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (report_id, area_key) DO NOTHING""",
                      (rep["id"], clave, _nota(a.get("puntaje")), [str(x) for x in (a.get("fuentes") or [])],
                       None if solo_notas else a.get("comentario"), bool(t), NIVEL_AREA.get(t.get("nivel")), t.get("ejercicio")))
        item = _items(c, u, sid)[0]
    return item


SQL_MIS = """
SELECT s.*, sc.slug, r.judges, r.final_report
FROM session s JOIN scenario sc ON sc.id = s.scenario_id
LEFT JOIN report r ON r.session_id = s.id AND r.is_current
WHERE s.user_id = %s AND s.status IN ('completed', 'abandoned') {extra}
ORDER BY s.created_at DESC
"""


def _items(c, u, sid=None):
    filas = c.execute(SQL_MIS.format(extra="AND s.id = %s" if sid else ""), (u["id"], sid) if sid else (u["id"],)).fetchall()
    salida = []
    for s in filas:
        quien = s["interviewee"] or {}
        salida.append({
            "id": str(s["id"]), "nombre": quien.get("first_name", ""), "apellido": quien.get("last_name", ""),
            "fecha": ms(s["created_at"]), "expira": ms(s["expires_at"]), "version": s["app_version"] or "",
            "tema": s["slug"], "tema_titulo": s["scenario_title"],
            "estado": "completa" if s["status"] == "completed" else "incompleta",
            "conservado": CONSERVAR_ES.get(s["retention_mode"], "completo"),
            "consentimiento": {"aceptado_en": ms(s["consented_at"]), "version": s["consent_text_version"]} if s["consent_accepted"] else None,
            "transcripcion": s["transcript"] or [], "informes": s["judges"] or {},
            "final": s["final_report"] if s["final_report"] else None,
        })
    return salida


def _purgar(c):
    return c.execute("SELECT purge_expired_sessions() AS n").fetchone()["n"]


@router.get("/api/sesiones")
def mis_sesiones(u=Depends(usuario_actual)):
    with conexion() as c:
        _purgar(c)
        return {"sesiones": _items(c, u)}


@router.delete("/api/sesiones/{sid}")
def borrar_sesion(sid: str, u=Depends(usuario_actual)):
    with conexion() as c:
        _sesion_propia(c, u, sid)
        c.execute("DELETE FROM session WHERE id = %s", (sid,))
    return {"ok": True}


@router.delete("/api/sesiones")
def borrar_mis_sesiones(u=Depends(usuario_actual)):
    with conexion() as c:
        n = c.execute("DELETE FROM session WHERE user_id = %s", (u["id"],)).rowcount
    return {"borradas": n}


# ------------------------------------------------------------------ agenda
class Agenda(BaseModel):
    fecha: Optional[dict] = None  # {y, m (0-11, como en la página), d}


@router.get("/api/agenda")
def ver_agenda(u=Depends(usuario_actual)):
    f = u["next_session_date"]
    return {"fecha": {"y": f.year, "m": f.month - 1, "d": f.day} if f else None}


@router.put("/api/agenda")
def fijar_agenda(d: Agenda, u=Depends(usuario_actual)):
    f = None
    if d.fecha:
        try:
            f = date(int(d.fecha["y"]), int(d.fecha["m"]) + 1, int(d.fecha["d"]))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Fecha no válida.")
    with conexion() as c:
        c.execute("UPDATE app_user SET next_session_date = %s WHERE id = %s", (f, u["id"]))
    return {"ok": True}


# ------------------------------------------------------------------ organización y retención
def _org(c, client_id):
    o = c.execute("SELECT * FROM client WHERE id = %s", (client_id,)).fetchone()
    return {"id": str(o["id"]), "nombre": o["name"], "retencion_dias": o["retention_days"], "conservar": CONSERVAR_ES[o["retention_mode"]]}


@router.get("/api/organizacion")
def ver_organizacion(u=Depends(usuario_actual)):
    with conexion() as c:
        return _org(c, u["client_id"])


class Retencion(BaseModel):
    dias: int
    conservar: str = Field(default="completo", max_length=20)


@router.put("/api/organizacion/retencion")
def guardar_retencion(d: Retencion, u=Depends(admin_actual)):
    if not 1 <= d.dias <= 365:
        raise HTTPException(status_code=400, detail="El plazo debe ser entre 1 y 365 días.")
    with conexion() as c:
        c.execute("UPDATE client SET retention_days = %s, retention_mode = %s WHERE id = %s",
                  (d.dias, CONSERVAR.get(d.conservar, "full"), u["client_id"]))
        # las entrevistas ya guardadas toman el plazo nuevo (contado desde su fecha); las que ya lo superan se borran
        c.execute("""UPDATE session s SET expires_at = s.created_at + make_interval(days => coalesce(sc.retention_days, %s))
                     FROM scenario sc WHERE sc.id = s.scenario_id AND s.client_id = %s""", (d.dias, u["client_id"]))
        _purgar(c)
        return _org(c, u["client_id"])


# ------------------------------------------------------------------ borrado de cuentas
@router.post("/api/cuenta/borrado")
def solicitar_borrado(u=Depends(usuario_actual)):
    with conexion() as c:
        c.execute("""INSERT INTO deletion_request (client_id, user_id, user_label) VALUES (%s, %s, %s)
                     ON CONFLICT (user_id) WHERE status = 'pending' DO NOTHING""",
                  (u["client_id"], u["id"], f"{u['display_name']} ({u['email']})"))
    return {"ok": True}


@router.get("/api/cuenta/borrado")
def mi_solicitud(u=Depends(usuario_actual)):
    with conexion() as c:
        r = c.execute("SELECT requested_at FROM deletion_request WHERE user_id = %s AND status = 'pending'", (u["id"],)).fetchone()
    return {"solicitud": {"solicitado": ms(r["requested_at"])} if r else None}


@router.get("/api/borrados")
def solicitudes(u=Depends(admin_actual)):
    with conexion() as c:
        filas = c.execute("""SELECT d.*, a.first_name, a.last_name, a.email FROM deletion_request d
                             LEFT JOIN app_user a ON a.id = d.user_id
                             WHERE d.client_id = %s ORDER BY d.requested_at DESC""", (u["client_id"],)).fetchall()
    return {"solicitudes": [{
        "id": str(r["id"]), "estado": "pendiente" if r["status"] == "pending" else "procesada",
        "nombre": f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() if r["status"] == "pending" else "Usuario borrado",
        "correo": r["email"] if r["status"] == "pending" else "", "solicitado": ms(r["requested_at"]), "procesado": ms(r["processed_at"]),
    } for r in filas]}


@router.post("/api/borrados/{rid}/procesar")
def procesar_borrado(rid: str, u=Depends(admin_actual)):
    with conexion() as c:
        try:
            r = c.execute("SELECT * FROM deletion_request WHERE id = %s AND client_id = %s AND status = 'pending'", (rid, u["client_id"])).fetchone()
        except Exception:
            r = None
        if not r:
            raise HTTPException(status_code=404, detail="Esa solicitud ya no está pendiente.")
        if r["user_id"] == u["id"]:
            raise HTTPException(status_code=400, detail="No puedes borrar tu propia cuenta de administrador desde aquí.")
        c.execute("UPDATE deletion_request SET status = 'processed', processed_at = now(), processed_by = %s, user_label = NULL WHERE id = %s",
                  (u["id"], rid))
        c.execute("DELETE FROM app_user WHERE id = %s", (r["user_id"],))  # sus entrevistas e informes caen en cascada
    return {"ok": True}


# ------------------------------------------------------------------ panel del cliente (a1)
@router.get("/api/panel")
def panel(u=Depends(admin_actual)):
    with conexion() as c:
        _purgar(c)
        org = _org(c, u["client_id"])
        temas = [escenario_pagina(r, False) for r in _escenarios(c, u["client_id"], True)]
        por_tema = {r["slug"]: r["n"] for r in c.execute(
            """SELECT sc.slug, count(*) AS n FROM session s JOIN scenario sc ON sc.id = s.scenario_id
               WHERE s.client_id = %s AND s.status = 'completed' GROUP BY sc.slug""", (u["client_id"],)).fetchall()}
        cifras = c.execute("""SELECT count(*) FILTER (WHERE s.created_at >= now() - interval '30 days') AS mes,
                              round(avg(r.overall_score), 1) AS media
                              FROM session s LEFT JOIN report r ON r.session_id = s.id AND r.is_current
                              WHERE s.client_id = %s AND s.status = 'completed'""", (u["client_id"],)).fetchone()
        voceros = c.execute("""SELECT a.first_name, a.last_name, a.display_name, a.email,
                               count(s.id) FILTER (WHERE s.status = 'completed') AS sesiones,
                               max(s.created_at) FILTER (WHERE s.status = 'completed') AS ultima
                               FROM app_user a LEFT JOIN session s ON s.user_id = a.id
                               WHERE a.client_id = %s AND a.role = 'spokesperson' AND a.status = 'active'
                               GROUP BY a.id ORDER BY a.created_at""", (u["client_id"],)).fetchall()
    for t in temas:
        t["sesiones"] = por_tema.get(t["id"], 0)
    return {
        "organizacion": org, "temas_activos": sum(1 for t in temas if t["activo"]), "voceros": len(voceros),
        "sesiones_mes": cifras["mes"] or 0, "global_medio": float(cifras["media"]) if cifras["media"] is not None else None,
        "temas": temas,
        "lista_voceros": [{"nombre": f"{v['first_name'] or v['display_name']} {v['last_name'] or ''}".strip(), "correo": v["email"],
                           "sesiones": v["sesiones"], "ultima": ms(v["ultima"])} for v in voceros],
    }

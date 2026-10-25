"""
VoxReady - backend para Vercel (FastAPI, SIN ESTADO).

Diferencias clave con la versión local (backend_entrevista.py):
  * No escribe nada en disco (en Vercel el disco es temporal y no se comparte
    entre peticiones). Todo lo que hace falta viaja en cada petición.
  * No usa librosa ni ffmpeg: la voz se mide en el navegador y aquí solo llegan
    las métricas (números). No se sube ningún audio.
  * Las claves de la IA salen de variables de entorno (GEMINI_API_KEY, ANTHROPIC_API_KEY
    y/o NVIDIA_API_KEY), nunca del código. Si un proveedor falla, se usa el siguiente.

Local:   python -m uvicorn app:app --reload --port 8000   ->  http://localhost:8000
Vercel:  se despliega tal cual (el objeto `app` es el punto de entrada).
"""

import json
import os
import statistics
import time
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field

try:  # en local lee el archivo .env; en Vercel las variables ya vienen del panel
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

VERSION = "37"  # súbela aquí y en public/index.html (VERSION_APP) cada vez que cambies algo
# NVIDIA retira modelos gratuitos con frecuencia. Por eso hay un modelo principal y respaldos:
# si el principal responde 404 (ya no existe), se prueba el siguiente automáticamente.
# Se cambian SIN tocar código, con variables de entorno en Vercel:
#   MODELO_IA          = modelo principal           (por defecto z-ai/glm-5.3)
#   MODELOS_RESPALDO   = respaldos separados por coma (por defecto z-ai/glm-5.3-flash)
MODELO = os.getenv("MODELO_IA", "z-ai/glm-5.3").strip()
MODELOS_RESPALDO = [m.strip() for m in os.getenv("MODELOS_RESPALDO", "z-ai/glm-5.3-flash").split(",") if m.strip()]
LISTA_MODELOS = [MODELO] + [m for m in MODELOS_RESPALDO if m != MODELO]
_modelo_activo = None  # el último que funcionó (por instancia del servidor)
BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

app = FastAPI(title="VoxReady", version=VERSION)

# ---------------------------------------------------------------- IA ---------
# Varios proveedores con RESPALDO: si el primero falla (caída, límite, lentitud, saldo),
# se usa el siguiente automáticamente. Se configura con variables de entorno en Vercel:
#   PROVEEDORES_IA     = orden separado por coma (por defecto "gemini,anthropic,groq,openrouter,nvidia").
#                        Solo se usan los que tengan clave.
#   GEMINI_API_KEY     = clave de Gemini (Google)
#   ANTHROPIC_API_KEY  = clave de Claude (Anthropic)
#   GROQ_API_KEY       = clave de Groq (gsk_...), gratis y muy rápido
#   OPENROUTER_API_KEY = clave de OpenRouter (sk-or-...), un solo acceso a muchos modelos
#   NVIDIA_API_KEY     = clave de NVIDIA (servicio de prueba gratuito) <- último respaldo


def _lista_env(nombre, por_defecto):
    return [m.strip() for m in os.getenv(nombre, por_defecto).split(",") if m.strip()]


PROVEEDORES_ORDEN = [p.lower() for p in _lista_env("PROVEEDORES_IA", "gemini,anthropic,groq,openrouter,nvidia")]
# Groq y OpenRouter también usan el formato OpenAI (no hace falta otro paquete).
# "openrouter/free" es un alias de OpenRouter que elige solo un modelo gratuito disponible,
# así no se rompe cuando retiran un modelo concreto. Se pueden cambiar con MODELOS_GROQ / MODELOS_OPENROUTER.
COMPAT = {
    "groq": {"clave": "GROQ_API_KEY", "url": os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
             "modelos": _lista_env("MODELOS_GROQ", "llama-3.3-70b-versatile,openai/gpt-oss-120b")},
    "openrouter": {"clave": "OPENROUTER_API_KEY", "url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
                   "modelos": _lista_env("MODELOS_OPENROUTER", "openrouter/free")},
}
# Gemini se usa por su endpoint compatible con OpenAI (no hace falta otro paquete).
# Los modelos de Gemini se retiran seguido: "gemini-flash-latest" es un alias que Google mantiene al día
# y "gemini-3.1-flash-lite" es el respaldo. Se pueden cambiar con MODELOS_GEMINI (separados por coma).
GEMINI_BASE_URL = os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai/")
MODELOS_GEMINI = _lista_env("MODELOS_GEMINI", "gemini-flash-latest,gemini-3.1-flash-lite")
_modelo_activo_gemini = None
# El entrevistador necesita rapidez; los jueces, calidad. Si el primer modelo de la lista no existe (404), se usa el siguiente.
MODELOS_ANTHROPIC_RAPIDO = _lista_env("MODELOS_ANTHROPIC_RAPIDO", "claude-haiku-4-5-20251001")
MODELOS_ANTHROPIC_EVALUADOR = _lista_env("MODELOS_ANTHROPIC_EVALUADOR", "claude-sonnet-5-5,claude-haiku-4-5-20251001")
TIEMPO_LIMITE = {"entrevistador": 15, "evaluador": 35, "prueba": 12}  # segundos POR proveedor
ENFRIAMIENTO_S = 60  # un proveedor que falló se salta durante este tiempo (si hay otro disponible)
ENFRIAMIENTO_CLAVE_S = 600  # si falló por clave/permisos (401/402/403), no se reintenta durante 10 min
_enfriar_hasta = {}
_clientes = {}


class ConfiguracionIA(Exception):
    """Problema de configuración del servidor (por ejemplo, falta un paquete). El mensaje se muestra tal cual."""


PREFIJO_CLAVE = {"GEMINI_API_KEY": ("AIza", "AQ."), "ANTHROPIC_API_KEY": "sk-ant-", "GROQ_API_KEY": "gsk_",
                 "OPENROUTER_API_KEY": "sk-or-", "NVIDIA_API_KEY": "nvapi-"}


def clave_de(nombre):
    """Lee una clave del entorno y la LIMPIA: quita espacios, saltos de línea y comillas.
    (Pegar la clave con un espacio o salto de línea al final es el error más común y hace que
    la conexión falle sin ningún código de error.)"""
    crudo = os.getenv(nombre) or ""
    return "".join(crudo.split()).strip("\"'")


def estado_claves():
    """Información SEGURA sobre las claves (nunca la clave): si existe, si se ve bien formada y si venía sucia."""
    info = {}
    for nombre, prefijo in PREFIJO_CLAVE.items():
        crudo = os.getenv(nombre) or ""
        limpia = clave_de(nombre)
        info[nombre] = {
            "configurada": bool(limpia),
            "formato_esperado": limpia.startswith(prefijo) if limpia else None,
            "tenia_espacios_o_comillas": bool(limpia) and limpia != crudo,
        }
    return info


def proveedores_configurados():
    claves = {"gemini": clave_de("GEMINI_API_KEY"), "anthropic": clave_de("ANTHROPIC_API_KEY"), "nvidia": clave_de("NVIDIA_API_KEY")}
    for nombre, cfg in COMPAT.items():
        claves[nombre] = clave_de(cfg["clave"])
    return [p for p in PROVEEDORES_ORDEN if claves.get(p)]


def _cliente_compat(prov):
    if prov not in _clientes:
        cfg = COMPAT[prov]
        _clientes[prov] = OpenAI(base_url=cfg["url"], api_key=clave_de(cfg["clave"]), timeout=60, max_retries=0)
    return _clientes[prov]


def _cliente_nvidia():
    if "nvidia" not in _clientes:
        _clientes["nvidia"] = OpenAI(base_url=BASE_URL, api_key=clave_de("NVIDIA_API_KEY"), timeout=100, max_retries=0)
    return _clientes["nvidia"]


def _cliente_gemini():
    if "gemini" not in _clientes:
        _clientes["gemini"] = OpenAI(base_url=GEMINI_BASE_URL, api_key=clave_de("GEMINI_API_KEY"), timeout=60, max_retries=0)
    return _clientes["gemini"]


def _cliente_anthropic():
    if "anthropic" not in _clientes:
        try:
            import anthropic  # se importa aquí para que la app arranque aunque el paquete falte
        except ImportError:
            raise ConfiguracionIA(
                "Falta el paquete 'anthropic' en el servidor: sube requirements.txt a GitHub (debe incluir la línea "
                "'anthropic') y haz Redeploy en Vercel."
            )
        _clientes["anthropic"] = anthropic.Anthropic(api_key=clave_de("ANTHROPIC_API_KEY"), timeout=60, max_retries=0)
    return _clientes["anthropic"]


def obtener_cliente():
    """Cliente de NVIDIA (se usa para listar su catálogo de modelos)."""
    if not clave_de("NVIDIA_API_KEY"):
        raise HTTPException(status_code=500, detail="Falta la variable de entorno NVIDIA_API_KEY.")
    return _cliente_nvidia()


def traducir_error_ia(e):
    """(código HTTP para nuestra página, mensaje claro). Siempre dice el código del proveedor
    para que, si el cliente manda una captura, se vea de inmediato qué pasó."""
    codigo = getattr(e, "status_code", None)
    nombre = type(e).__name__
    texto = str(e).lower()
    if isinstance(e, ConfiguracionIA):
        return 500, str(e)
    if codigo == 429:
        return 429, "La IA recibió demasiadas solicitudes seguidas o se alcanzó el límite del proveedor (código 429). Espera un minuto y reintenta."
    if "ip address restriction" in texto:
        return 500, "La clave de Gemini tiene una restricción por dirección IP y el servidor de Vercel usa IPs que cambian (código 403). Hay que crear una clave sin restricción de IP."
    if "referer" in texto or "referrer" in texto:
        return 500, "La clave de Gemini está restringida a ciertos sitios web (código 403) y no sirve para el servidor. Hay que crear una clave sin esa restricción."
    if "leaked" in texto:
        return 500, "Google desactivó la clave de Gemini por haberse expuesto públicamente (código 403). Hay que crear una nueva."
    if codigo == 403 and ("has not been used in project" in texto or "service_disabled" in texto or "is disabled" in texto):
        return 500, "La API de Gemini (Generative Language) no está habilitada en el proyecto de Google de esa clave (código 403)."
    if codigo == 400 and ("api key not valid" in texto or "api_key_invalid" in texto):
        return 500, "El servicio de IA rechazó la clave (código 400: clave no válida). Avisa al administrador."
    if codigo == 402 or (codigo == 400 and "credit balance" in texto):
        return 500, f"Se agotó el saldo o crédito del servicio de IA (código {codigo}). Avisa al administrador."
    if codigo in (401, 403):
        return 500, f"El servicio de IA rechazó las credenciales (código {codigo}). Avisa al administrador."
    if codigo == 404:
        return 500, "El modelo de IA no está disponible (código 404): puede haber sido retirado o tu cuenta no tiene acceso. Avisa al administrador."
    if codigo and codigo >= 500:
        return 502, f"El proveedor de IA tuvo un error temporal (código {codigo}). Reintenta en unos segundos."
    if "Timeout" in nombre:
        return 504, "La IA tardó demasiado en responder. Reintenta en unos segundos."
    if "Connection" in nombre:
        return 502, "No se pudo conectar con el proveedor de IA: falla de red, o la clave quedó mal copiada en Vercel (con espacios o saltos de línea). Reintenta; si se repite, vuelve a pegar la clave."
    if isinstance(e, RuntimeError) and "vacía" in texto:
        return 502, "La IA devolvió una respuesta vacía. Reintenta en unos segundos."
    return 502, "No se pudo contactar a la IA. Reintenta en unos segundos."


def detalle_proveedor(e):
    """Texto del error del proveedor (sin claves), con el tipo de error y su causa, para diagnosticar."""
    cuerpo = getattr(e, "body", None)
    if cuerpo:
        base = str(cuerpo)[:300]
    else:
        try:
            base = e.response.text[:300]
        except Exception:
            base = str(e)[:300]
    causa = getattr(e, "__cause__", None)
    extra = f" | causa: {type(causa).__name__}: {str(causa)[:150]}" if causa else ""
    return f"{type(e).__name__}: {base}{extra}"[:520]


def _extra_body(modelo, esfuerzo):
    """Los parámetros de razonamiento son propios de los modelos GLM; a los demás no se les envían."""
    if "glm" in modelo.lower():
        return {"chat_template_kwargs": {"clear_thinking": True, "reasoning_effort": esfuerzo}}
    return None


def _llamar_anthropic(modelo, mensajes, temperature, max_tokens, timeout):
    cliente = _cliente_anthropic().with_options(timeout=timeout, max_retries=0)
    sistema = "\n\n".join(m["content"] for m in mensajes if m["role"] == "system")
    conversacion = [{"role": m["role"], "content": m["content"]} for m in mensajes if m["role"] in ("user", "assistant")]
    kwargs = dict(model=modelo, max_tokens=max_tokens, messages=conversacion)
    if sistema:
        kwargs["system"] = sistema
    try:
        # temperature va por extra_body: algunas versiones de la librería (o modelos) ya no la aceptan como parámetro directo
        r = cliente.messages.create(extra_body={"temperature": temperature}, **kwargs)
    except TypeError:
        r = cliente.messages.create(**kwargs)
    except Exception as e:
        # Si el modelo rechaza temperature (400), se reintenta sin ella
        if getattr(e, "status_code", None) == 400 and "temperature" in str(e).lower():
            r = cliente.messages.create(**kwargs)
        else:
            raise
    return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")


def _llamar_gemini(modelo, mensajes, temperature, max_tokens, esfuerzo, timeout):
    cliente = _cliente_gemini().with_options(timeout=timeout, max_retries=0)
    # Los modelos Gemini "piensan" y ese razonamiento cuenta dentro de max_tokens: se da margen para que no quede vacía la respuesta
    kwargs = dict(model=modelo, messages=mensajes, temperature=temperature, max_tokens=int(max_tokens * 2))
    try:
        r = cliente.chat.completions.create(reasoning_effort="low" if esfuerzo == "low" else "medium", **kwargs)
    except Exception as e:
        # Si el modelo no acepta ese parámetro, se reintenta sin él
        if getattr(e, "status_code", None) == 400 and ("reasoning" in str(e).lower() or "thinking" in str(e).lower()):
            r = cliente.chat.completions.create(**kwargs)
        else:
            raise
    return r.choices[0].message.content or ""


def _llamar_compat(prov, modelo, mensajes, temperature, max_tokens, timeout):
    """Groq y OpenRouter. Los modelos con razonamiento gastan tokens pensando: se da margen para que la respuesta no quede vacía."""
    cliente = _cliente_compat(prov).with_options(timeout=timeout, max_retries=0)
    r = cliente.chat.completions.create(model=modelo, messages=mensajes, temperature=temperature, max_tokens=int(max_tokens * 2))
    return r.choices[0].message.content or ""


def _llamar_nvidia(modelo, mensajes, temperature, max_tokens, esfuerzo, timeout):
    cliente = _cliente_nvidia().with_options(timeout=timeout, max_retries=0)
    kwargs = dict(model=modelo, messages=mensajes, temperature=temperature, max_tokens=max_tokens)
    extra = _extra_body(modelo, esfuerzo)
    if extra:
        kwargs["extra_body"] = extra
    r = cliente.chat.completions.create(**kwargs)
    return r.choices[0].message.content or ""


def _intentar_proveedor(prov, mensajes, temperature, max_tokens, esfuerzo, rol, timeout, exigir_texto=True):
    """Prueba los modelos de UN proveedor en orden y salta al siguiente SOLO si el modelo no existe (404).
    Devuelve (texto, modelo_usado); si falla, lanza la excepción."""
    global _modelo_activo, _modelo_activo_gemini
    if prov == "anthropic":
        modelos = MODELOS_ANTHROPIC_EVALUADOR if rol == "evaluador" else MODELOS_ANTHROPIC_RAPIDO
    elif prov == "gemini":
        modelos = ([_modelo_activo_gemini] if _modelo_activo_gemini else []) + [m for m in MODELOS_GEMINI if m != _modelo_activo_gemini]
    elif prov in COMPAT:
        modelos = COMPAT[prov]["modelos"]
    else:
        modelos = ([_modelo_activo] if _modelo_activo else []) + [m for m in LISTA_MODELOS if m != _modelo_activo]
    for i, modelo in enumerate(modelos):
        try:
            if prov == "anthropic":
                texto = _llamar_anthropic(modelo, mensajes, temperature, max_tokens, timeout)
            elif prov == "gemini":
                texto = _llamar_gemini(modelo, mensajes, temperature, max_tokens, esfuerzo, timeout)
            elif prov in COMPAT:
                texto = _llamar_compat(prov, modelo, mensajes, temperature, max_tokens, timeout)
            else:
                texto = _llamar_nvidia(modelo, mensajes, temperature, max_tokens, esfuerzo, timeout)
            if exigir_texto and not texto.strip():
                raise RuntimeError("la IA devolvió una respuesta vacía")
            if prov == "nvidia":
                _modelo_activo = modelo
            elif prov == "gemini":
                _modelo_activo_gemini = modelo
            return texto, modelo
        except Exception as e:
            if getattr(e, "status_code", None) == 404 and i < len(modelos) - 1:
                print("Modelo no disponible (404), probando el siguiente:", prov, modelo)
                if prov == "nvidia" and modelo == _modelo_activo:
                    _modelo_activo = None
                if prov == "gemini" and modelo == _modelo_activo_gemini:
                    _modelo_activo_gemini = None
                continue
            raise


def generar_texto(mensajes, temperature, max_tokens, esfuerzo, rol="evaluador"):
    """Devuelve (texto, proveedor, modelo). Si un proveedor falla, usa el siguiente.
    Lanza HTTPException solo si NINGUNO responde."""
    todos = proveedores_configurados()
    if not todos:
        raise HTTPException(
            status_code=500,
            detail=(
                "No hay ninguna clave de IA configurada. En Vercel agrega GEMINI_API_KEY, GROQ_API_KEY, "
                "ANTHROPIC_API_KEY u OPENROUTER_API_KEY (Project -> Settings -> Environment Variables) y vuelve a desplegar."
            ),
        )
    ahora = time.time()
    candidatos = [p for p in todos if _enfriar_hasta.get(p, 0) <= ahora] or todos
    timeout = TIEMPO_LIMITE.get(rol, 45)
    fallos, ultimo = [], None
    for prov in candidatos:
        try:
            texto, modelo = _intentar_proveedor(prov, mensajes, temperature, max_tokens, esfuerzo, rol, timeout)
            _enfriar_hasta.pop(prov, None)
            if fallos:
                print("IA: respondió el respaldo", prov, "tras fallar:", fallos)
            return texto, prov, modelo
        except Exception as e:
            ultimo = e
            permanente = getattr(e, "status_code", None) in (401, 402, 403) or isinstance(e, ConfiguracionIA)
            _enfriar_hasta[prov] = time.time() + (ENFRIAMIENTO_CLAVE_S if permanente else ENFRIAMIENTO_S)
            fallos.append(f"{prov}: {type(e).__name__} {getattr(e, 'status_code', '') or ''}".strip())
            # Queda en los logs de Vercel (Project -> Logs)
            print("Error llamando a la IA:", prov, type(e).__name__, getattr(e, "status_code", None), repr(e)[:300])
    codigo, detalle = traducir_error_ia(ultimo)
    if len(fallos) > 1:
        detalle += " [intentos: " + "; ".join(fallos) + "]"
    raise HTTPException(status_code=codigo, detail=detalle)


def llamar_ia(mensajes, temperature, max_tokens, esfuerzo, rol="evaluador"):
    texto, _proveedor, _modelo = generar_texto(mensajes, temperature, max_tokens, esfuerzo, rol)
    return texto


def extraer_json(texto):
    limpio = texto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    if inicio != -1 and fin > inicio:
        limpio = limpio[inicio : fin + 1]
    try:
        return json.loads(limpio)
    except json.JSONDecodeError:
        raise HTTPException(status_code=502, detail="La IA no devolvió un informe válido. Intenta de nuevo.")


# ------------------------------------------------- Límite anti-abuso ----------
# Es un freno básico por IP (en memoria, por instancia). Para algo más fuerte usa
# el Firewall / rate limiting del panel de Vercel.

VENTANA_S = 600
MAX_LLAMADAS = int(os.getenv("LIMITE_LLAMADAS", "100"))
_registro = defaultdict(deque)


def limitar(request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else "desconocida"
    )
    ahora = time.time()
    cola = _registro[ip]
    while cola and ahora - cola[0] > VENTANA_S:
        cola.popleft()
    if len(cola) >= MAX_LLAMADAS:
        raise HTTPException(status_code=429, detail="Demasiadas solicitudes seguidas. Espera unos minutos.")
    cola.append(ahora)


# ------------------------------------------------------------ Modelos ---------


class Mensaje(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=4000)


class PeticionEntrevista(BaseModel):
    historial: list[Mensaje] = Field(min_length=1, max_length=40)


class PeticionConversacion(BaseModel):
    conversacion: list[Mensaje] = Field(min_length=1, max_length=40)


class MetricasVoz(BaseModel):
    duracion_segundos: float
    tono_promedio_hz: float
    tono_variabilidad_hz: float
    volumen_promedio: float
    volumen_variabilidad: float
    proporcion_silencio: float
    numero_pausas: int
    demora_media_antes_de_hablar_s: Optional[float] = None
    respuestas_con_voz: Optional[int] = None
    habla_total_segundos: Optional[float] = None
    fiabilidad_muestra: Optional[str] = None
    # De la calibración (cómo suena ESTA persona normalmente); pueden no venir
    tono_referencia_hz: Optional[float] = None
    volumen_referencia: Optional[float] = None


class PeticionVoz(BaseModel):
    metricas: MetricasVoz


class MuestraCara(BaseModel):
    t: float
    feliz: float
    triste: float
    enojo: float
    sorpresa: float
    miedo: float
    asco: float
    # Expresiones nuevas (v36); si la página no las manda valen 0
    desprecio: float = 0.0
    tension: float = 0.0
    sonrisa_falsa: float = 0.0
    duda: float = 0.0
    yaw: float
    pitch: float
    mirada: float


class CalibracionCara(BaseModel):
    calibrada: bool = False
    usa_lentes: bool = False
    ojos_fiables: bool = True  # False si en la calibración no se vio un parpadeo claro (lentes, reflejos, luz)


class PeticionCara(BaseModel):
    muestras: list[MuestraCara] = Field(min_length=1, max_length=4000)
    calibracion: Optional[CalibracionCara] = None
    parpadeos: int = 0
    frames_totales: int = 0
    frames_con_rostro: int = 0
    duracion_segundos: float = 0


# ------------------------------------------------- Entrevistador --------------

SYSTEM_PROMPT = (
    "Eres un periodista exigente entrevistando a un vocero de una empresa "
    "durante una crisis (un derrame químico que afectó el agua de tres "
    "comunidades). Haz una pregunta a la vez, nunca varias juntas. "
    "Escucha la respuesta del vocero y repregunta sobre lo que dijo: si "
    "evade, presiona; si da un dato nuevo, profundiza en ese dato. "
    "Sé firme pero profesional, como un periodista real, no un robot. "
    "Máximo 2 frases cortas por turno."
)


@app.post("/api/entrevistar", dependencies=[Depends(limitar)])
def entrevistar(peticion: PeticionEntrevista):
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes += [{"role": m.role, "content": m.content} for m in peticion.historial]
    texto = llamar_ia(mensajes, temperature=0.7, max_tokens=500, esfuerzo="low", rol="entrevistador")
    return {"pregunta": texto}


def probar_proveedor(prov):
    """Llamada mínima a UN proveedor (sin respaldo), para el diagnóstico."""
    t0 = time.time()
    try:
        _t, modelo = _intentar_proveedor(prov, [{"role": "user", "content": "Responde solo: ok"}], 0, 16, "low", "prueba", TIEMPO_LIMITE["prueba"], exigir_texto=False)
        return {"proveedor": prov, "estado": "ok", "modelo": modelo, "segundos": round(time.time() - t0, 1)}
    except Exception as e:
        codigo, detalle = traducir_error_ia(e)
        return {
            "proveedor": prov,
            "estado": "error",
            "codigo_http": codigo,
            "codigo_proveedor": getattr(e, "status_code", None),
            "tipo": type(e).__name__,
            "detalle": detalle,
            "mensaje_proveedor": detalle_proveedor(e),
            "segundos": round(time.time() - t0, 1),
        }


@app.get("/api/salud")
def salud(request: Request, probar_ia: int = 0, modelos: str = ""):
    """Estado del servidor.
    ?probar_ia=1     prueba CADA proveedor de IA por separado (ok / degradado / error).
    ?modelos=flash   lista los modelos de NVIDIA y de Gemini que contienen ese texto (?modelos=todos: todos)."""
    configurados = proveedores_configurados()
    info = {
        "estado": "ok",
        "version": VERSION,
        "clave_configurada": bool(configurados),
        "proveedores": configurados,
        "claves": estado_claves(),
        "orden_de_proveedores": PROVEEDORES_ORDEN,
        "modelos_gemini": MODELOS_GEMINI,
        "modelos_groq": COMPAT["groq"]["modelos"],
        "modelos_openrouter": COMPAT["openrouter"]["modelos"],
        "modelos_anthropic": {"entrevistador": MODELOS_ANTHROPIC_RAPIDO, "evaluador": MODELOS_ANTHROPIC_EVALUADOR},
        "modelo": MODELO,
        "modelos_configurados": LISTA_MODELOS,
        "region": os.getenv("VERCEL_REGION") or "local",
    }
    if probar_ia:
        limitar(request)
        if not configurados:
            info["ia"] = {"estado": "error", "proveedores": [], "detalle": "No hay ninguna clave de IA configurada (GEMINI_API_KEY, GROQ_API_KEY, ANTHROPIC_API_KEY, OPENROUTER_API_KEY o NVIDIA_API_KEY)."}
        else:
            # En paralelo: el diagnóstico tarda lo del proveedor más lento, no la suma de todos
            with ThreadPoolExecutor(max_workers=len(configurados)) as pool:
                resultados = list(pool.map(probar_proveedor, configurados))
            funcionan = [r for r in resultados if r["estado"] == "ok"]
            estado = "error" if not funcionan else ("ok" if resultados[0]["estado"] == "ok" else "degradado")
            info["ia"] = {"estado": estado, "proveedores": resultados}
            if funcionan:
                info["ia"]["modelo"] = funcionan[0]["modelo"]
    if modelos:
        limitar(request)
        try:
            ids = sorted(m.id for m in obtener_cliente().models.list().data)
            filtro = modelos.lower()
            coincide = ids if filtro in ("1", "todos", "all") else [i for i in ids if filtro in i.lower()]
            info["catalogo"] = {
                "total_en_catalogo": len(ids),
                "filtro": modelos,
                "coincidencias": coincide[:80],
                "configurados_en_catalogo": {m: (m in ids) for m in LISTA_MODELOS},
            }
        except HTTPException as e:
            info["catalogo"] = {"error": e.detail}
        except Exception as e:
            codigo, detalle = traducir_error_ia(e)
            info["catalogo"] = {"error": detalle, "codigo_proveedor": getattr(e, "status_code", None), "mensaje_proveedor": detalle_proveedor(e)}
        if clave_de("GEMINI_API_KEY"):
            try:
                ids = sorted(m.id.replace("models/", "") for m in _cliente_gemini().models.list().data)
                filtro = modelos.lower()
                coincide = ids if filtro in ("1", "todos", "all") else [i for i in ids if filtro in i.lower()]
                info["catalogo_gemini"] = {
                    "total_en_catalogo": len(ids),
                    "filtro": modelos,
                    "coincidencias": coincide[:80],
                    "configurados_en_catalogo": {m: (m in ids) for m in MODELOS_GEMINI},
                }
            except Exception as e:
                codigo, detalle = traducir_error_ia(e)
                info["catalogo_gemini"] = {"error": detalle, "codigo_proveedor": getattr(e, "status_code", None), "mensaje_proveedor": detalle_proveedor(e)}
    return info


# ------------------------------------------------ Evaluación: CONTENIDO -------

RUBRICA = """
Eres un evaluador MUY EXIGENTE de vocerías de crisis. Evalúa la entrevista de un
vocero (un derrame químico afectó el agua de tres comunidades) en estas 4 áreas.
Nota de 0 a 5 (se permiten decimales de 0,5 en 0,5).

ESCALA (aplícala con rigor; la mayoría de las prácticas reales están entre 2 y 3,5):
- 0   = no respondió, escribió texto sin sentido, o nada de lo que dijo tiene relación con la pregunta.
- 1   = respuestas confusas, fuera de tema o que no dicen nada útil.
- 2   = respuestas vagas, evasivas o muy incompletas; el periodista no obtiene información.
- 3   = aceptable: responde, pero con vacíos, generalidades o titubeos.
- 4   = bueno: claro, directo y con datos o compromisos concretos, con fallos menores.
- 5   = excelente: impecable en todas las respuestas, nivel profesional. Es MUY raro: no lo uses salvo que sea evidente.

REGLAS DE RIGOR:
- Una respuesta corta, genérica o de relleno ("estamos trabajando en ello", "lamentamos lo ocurrido") sin datos ni acciones NO merece más de 2.
- Si el vocero no contesta lo que se le preguntó, la nota de "Transparencia vs. evasión" es 2 o menos.
- Si no hay ninguna respuesta con contenido real, las 4 notas son 0 o 1.
- No premies el esfuerzo ni la buena educación: premia solo lo que de verdad ayudaría a manejar la crisis.
- Cada comentario DEBE incluir una cita textual corta (entre comillas) de lo que dijo el vocero como evidencia de la nota. Si no hay nada que citar, di que no hay evidencia y baja la nota.

ÁREAS:
1. Claridad del mensaje: ¿las respuestas son entendibles y van al punto?
2. Transparencia vs. evasión: ¿responde directamente o esquiva las preguntas
   incómodas con lenguaje vago?
3. Manejo de la presión: ¿mantiene la calma y el control cuando el
   periodista insiste o lo presiona?
4. Responsabilidad y tono: ¿asume responsabilidad cuando corresponde, sin
   sonar defensivo ni evasivo?

Responde ÚNICAMENTE con un objeto JSON válido, sin texto antes ni después,
sin bloques de código markdown, con esta forma exacta:

{
  "puntaje_global": <número de 0 a 5>,
  "areas": [
    {"nombre": "Claridad del mensaje", "puntaje": <0-5>, "comentario": "... (con cita textual)"},
    {"nombre": "Transparencia vs. evasión", "puntaje": <0-5>, "comentario": "... (con cita textual)"},
    {"nombre": "Manejo de la presión", "puntaje": <0-5>, "comentario": "... (con cita textual)"},
    {"nombre": "Responsabilidad y tono", "puntaje": <0-5>, "comentario": "... (con cita textual)"}
  ],
  "resumen": "2-3 frases con la evaluación general",
  "recomendacion_principal": "1 frase con lo más importante a mejorar"
}
"""

# ------------------------------------------------------------------------------
# Topes por CÓDIGO: la IA tiende a ser generosa, así que lo evidente (respuestas vacías,
# sin sentido o demasiado cortas) se limita aquí, sin depender de lo que "opine" el modelo.
# ------------------------------------------------------------------------------
PALABRAS_MINIMAS = 8  # una respuesta con menos palabras que esto se considera de relleno


def _es_sin_sentido(texto):
    """Detecta texto basura: teclazos, repeticiones, casi sin letras o sin vocales."""
    t = texto.strip().lower()
    if not t:
        return True
    letras = [c for c in t if c.isalpha()]
    # ("sí", "ok", "no" son respuestas cortas pero reales: solo es basura si casi no hay letras)
    if not letras or len(letras) / max(len(t.replace(" ", "")), 1) < 0.6:
        return True
    palabras = [w for w in "".join(c if c.isalpha() or c.isspace() else " " for c in t).split() if w]
    if not palabras:
        return True
    vocales = set("aeiouáéíóúü")
    sin_vocal = [w for w in palabras if len(w) >= 4 and not (set(w) & vocales)]
    if len(sin_vocal) / len(palabras) > 0.4:
        return True
    if len(palabras) >= 4 and len(set(palabras)) / len(palabras) < 0.4:
        return True
    if any(len(set(w)) <= 2 and len(w) >= 6 for w in palabras):  # "aaaaaa", "ababab"
        return True
    return False


def estadisticas_respuestas(conversacion):
    respuestas = [m.content for m in conversacion if m.role == "user"]
    n = len(respuestas)
    if n == 0:
        return {"respuestas": 0, "pct_sin_sentido": 100.0, "pct_cortas": 100.0, "palabras_promedio": 0.0}
    palabras = [len(r.split()) for r in respuestas]
    sin_sentido = sum(1 for r in respuestas if _es_sin_sentido(r))
    cortas = sum(1 for p in palabras if p < PALABRAS_MINIMAS)
    return {
        "respuestas": n,
        "pct_sin_sentido": round(sin_sentido / n * 100, 1),
        "pct_cortas": round(cortas / n * 100, 1),
        "palabras_promedio": round(sum(palabras) / n, 1),
    }


def aplicar_topes_contenido(informe, est):
    """Limita las notas según lo evidente. Devuelve la lista de topes aplicados (para mostrarlos)."""
    if not isinstance(informe, dict) or not isinstance(informe.get("areas"), list):
        return []
    sin_sentido, cortas, prom = est["pct_sin_sentido"], est["pct_cortas"], est["palabras_promedio"]
    tope, motivo = 5.0, None
    if sin_sentido >= 80:
        tope, motivo = 0.0, "casi todas tus respuestas no tienen sentido o están vacías"
    elif sin_sentido >= 50:
        tope, motivo = 1.0, "la mayoría de tus respuestas no tienen sentido"
    elif sin_sentido > 0 and sin_sentido >= 25:
        tope, motivo = 2.0, "varias de tus respuestas no tienen sentido"
    elif cortas >= 80 or prom < 5:
        tope, motivo = 1.5, "casi todas tus respuestas son demasiado cortas para evaluar"
    elif cortas >= 50:
        tope, motivo = 2.5, "la mayoría de tus respuestas son muy cortas"
    elif prom < 15:
        tope, motivo = 3.5, "tus respuestas son breves y con poco desarrollo"
    if tope >= 5.0:
        return []
    cambios = False
    suma = []
    for a in informe["areas"]:
        try:
            p = float(a.get("puntaje"))
        except (TypeError, ValueError):
            continue
        if p > tope:
            a["puntaje"] = tope
            cambios = True
        suma.append(float(a["puntaje"]))
    if suma:
        informe["puntaje_global"] = round(sum(suma) / len(suma), 1)
    if cambios:
        informe["tope_aplicado"] = f"La nota se limitó a {tope:g}/5 porque {motivo}."
        return [informe["tope_aplicado"]]
    return []


@app.post("/api/evaluar", dependencies=[Depends(limitar)])
def evaluar(peticion: PeticionConversacion):
    lineas = []
    for m in peticion.conversacion:
        quien = "Entrevistador" if m.role == "assistant" else "Vocero"
        lineas.append(f"{quien}: {m.content}")
    texto = "\n".join(lineas)

    bruto = llamar_ia(
        [
            {"role": "system", "content": RUBRICA},
            {"role": "user", "content": f"Transcripción de la entrevista:\n\n{texto}"},
        ],
        temperature=0.1,
        max_tokens=1800,
        esfuerzo="high",
    )
    informe = extraer_json(bruto)
    est = estadisticas_respuestas(peticion.conversacion)
    aplicar_topes_contenido(informe, est)
    informe["estadisticas_respuestas"] = est
    return informe


# ---------------------------------------------------- Evaluación: VOZ ---------

RUBRICA_VOZ = """
Vas a evaluar la VOZ de un vocero durante una entrevista de crisis, a
partir de métricas acústicas ya calculadas (no del contenido de lo que dijo).
Las métricas se midieron en su navegador SOLO mientras respondía (no incluyen
el tiempo de espera de la IA) y el silencio se mide entre que empieza y termina
de hablar cada respuesta.

Te entrego:
- duración hablada total en segundos
- tono promedio (Hz) y su variabilidad (más variabilidad = más expresivo;
  muy poca = suena monótono). Si el tono viene en 0, no se pudo estimar: no lo evalúes.
- volumen promedio y su variabilidad (escala 0 a 1; consistencia de la proyección de voz)
- proporción de silencio y número de pausas de 0,4 s o más (pausas moderadas
  transmiten control; demasiado silencio o pausas muy largas transmiten inseguridad)
- demora_media_antes_de_hablar_s: segundos que tardó en empezar a responder
  (menos de 3 s transmite seguridad; más de 6 s transmite duda)
- respuestas_con_voz: cuántas respuestas fueron habladas
- habla_total_segundos y fiabilidad_muestra ("baja" si habló menos de 10 s,
  "media" hasta 30 s, "alta" más): si la fiabilidad es baja o media, EVALÚA IGUAL
  con lo que hay, pero dilo en el resumen (por ejemplo "respuestas muy cortas, la
  medición es orientativa"), usa notas moderadas salvo que algo sea muy evidente
  y no inventes tendencias que no se puedan ver en una muestra tan corta.

- tono_referencia_hz y volumen_referencia (si vienen): cómo suena ESTA persona
  normalmente, medido antes en una calibración leyendo una frase. Compara contra
  eso: un tono o un volumen muy distinto de su referencia puede indicar tensión o
  nervios. No juzgues su tono contra un tono "ideal": hay voces graves y agudas.

Evalúa 3 áreas (nota de 1 a 5, donde 5 es excelente):
1. Modulación del tono: ¿suena expresivo o monótono?
2. Ritmo y pausas: ¿el ritmo transmite control, o suena nervioso/entrecortado?
3. Proyección y volumen: ¿el volumen es consistente y firme?

Son estimaciones automáticas y aproximadas: no exageres conclusiones.

Responde ÚNICAMENTE con este JSON, sin texto adicional ni bloques markdown:
{
  "puntaje_global_voz": <1-5>,
  "areas": [
    {"nombre": "Modulación del tono", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Ritmo y pausas", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Proyección y volumen", "puntaje": <1-5>, "comentario": "..."}
  ],
  "resumen": "2-3 frases",
  "recomendacion_principal": "1 frase"
}
"""


@app.post("/api/evaluar_voz", dependencies=[Depends(limitar)])
def evaluar_voz(peticion: PeticionVoz):
    metricas = peticion.metricas.model_dump(exclude_none=True)
    bruto = llamar_ia(
        [
            {"role": "system", "content": RUBRICA_VOZ},
            {"role": "user", "content": f"Métricas de la entrevista:\n{json.dumps(metricas, ensure_ascii=False, indent=2)}"},
        ],
        temperature=0.3,
        max_tokens=1000,
        esfuerzo="high",
    )
    informe = extraer_json(bruto)
    informe["metricas"] = metricas
    return informe


# ---------------------------------------------------- Evaluación: CARA --------

EMOCIONES_CARA = ["feliz", "triste", "enojo", "sorpresa", "miedo", "asco", "desprecio", "tension", "sonrisa_falsa", "duda"]
UMBRAL_EMOCION_MARCADA = 0.30  # debe coincidir con UMBRAL_NEUTRAL de la página
UMBRAL_MIRADA_FUERA = 0.35
MARGEN_DUDOSA = 0.12  # si la 1ª y la 2ª expresión se parecen más que esto, la lectura es "dudosa"
SEGUNDOS_MINIMOS_EXPRESION = 0.6  # una expresión debe sostenerse este tiempo para contarse (evita gestos fugaces)


def _media(xs):
    return sum(xs) / len(xs)


def _desv(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def resumir_cara(p: PeticionCara):
    m = [x.model_dump() for x in p.muestras]
    n = len(m)

    duracion = p.duracion_segundos or ((m[-1]["t"] - m[0]["t"]) if n > 1 else 0)

    # Etiqueta de cada muestra: la expresión dominante, "neutral" si ninguna llega al umbral,
    # y marcada como dudosa si la segunda va casi pareja con la primera.
    etiquetas, dudosas = [], 0
    for x in m:
        orden = sorted(EMOCIONES_CARA, key=lambda e: x[e], reverse=True)
        top, segunda = orden[0], orden[1]
        if x[top] < UMBRAL_EMOCION_MARCADA:
            etiquetas.append("neutral")
        else:
            etiquetas.append(top)
            if x[top] - x[segunda] < MARGEN_DUDOSA and x[segunda] >= UMBRAL_EMOCION_MARCADA * 0.8:
                dudosas += 1
    # Solo cuenta una expresión si se sostiene al menos SEGUNDOS_MINIMOS_EXPRESION (las muestras van ~cada 0,2 s);
    # los gestos más fugaces se tratan como neutral para no inflar falsos positivos.
    paso = (m[-1]["t"] - m[0]["t"]) / (n - 1) if n > 1 and m[-1]["t"] > m[0]["t"] else 0.2
    minimo = max(1, round(SEGUNDOS_MINIMOS_EXPRESION / max(paso, 0.05)))
    filtradas, i = list(etiquetas), 0
    while i < n:
        j = i
        while j < n and etiquetas[j] == etiquetas[i]:
            j += 1
        if etiquetas[i] != "neutral" and (j - i) < minimo:
            for k in range(i, j):
                filtradas[k] = "neutral"
        i = j
    conteo = {e: 0 for e in EMOCIONES_CARA}
    conteo["neutral"] = 0
    for e in filtradas:
        conteo[e] += 1
    porcentaje_tiempo = {k: round(v / n * 100, 1) for k, v in conteo.items()}
    pct_dudosa = round(dudosas / n * 100, 1)

    intensidad = {e: round(_media([x[e] for x in m]) * 100, 1) for e in EMOCIONES_CARA}
    expresividad = _media([max(x[e] for e in EMOCIONES_CARA) for x in m]) * 100

    yaw = [x["yaw"] for x in m]
    pitch = [x["pitch"] for x in m]
    desalineacion = _media([(a * a + b * b) ** 0.5 for a, b in zip(yaw, pitch)])
    variabilidad = (_desv(yaw) ** 2 + _desv(pitch) ** 2) ** 0.5
    mirada_fuera = _media([1.0 if x["mirada"] > UMBRAL_MIRADA_FUERA else 0.0 for x in m]) * 100
    cal = p.calibracion or CalibracionCara()

    pct_rostro = (p.frames_con_rostro / p.frames_totales * 100) if p.frames_totales else 100.0
    parpadeos_min = round(p.parpadeos / (duracion / 60), 1) if duracion >= 10 else None
    if not cal.ojos_fiables:  # los ojos no se leen bien (lentes, reflejos): mejor no medir que medir mal
        parpadeos_min = None

    return {
        "duracion_segundos": round(duracion, 1),
        "muestras_analizadas": n,
        "pct_rostro_visible": round(min(pct_rostro, 100.0), 1),
        "porcentaje_tiempo_por_expresion": porcentaje_tiempo,
        "lectura_dudosa_pct": pct_dudosa,
        "intensidad_media_por_emocion_0a100": intensidad,
        "expresividad_media_0a100": round(expresividad, 1),
        "desalineacion_cabeza": round(desalineacion, 3),
        "variabilidad_cabeza": round(variabilidad, 3),
        "mirada_fuera_de_camara_pct": round(mirada_fuera, 1) if cal.ojos_fiables else None,
        "parpadeos_por_minuto": parpadeos_min,
        "calibracion": {"calibrada": cal.calibrada, "usa_lentes": cal.usa_lentes, "ojos_fiables": cal.ojos_fiables},
    }


RUBRICA_CARA = """
Vas a evaluar la EXPRESIÓN FACIAL y la presencia de un vocero durante una
entrevista de crisis (un derrame químico que afectó el agua de tres
comunidades), a partir de métricas calculadas por visión por computador
(no del contenido de lo que dijo ni de su voz).

El objetivo del vocero es verse PROFESIONAL y SEGURO: sereno, serio y
empático, sin sonrisas fuera de lugar, sin gestos de enojo o desprecio y sin
señales de nerviosismo.

Te entrego:
- pct_rostro_visible: % del tiempo en que se detectó su cara (si es menor a
  70, advierte que la medición es poco fiable)
- porcentaje_tiempo_por_expresion: % del tiempo que cada expresión fue la
  dominante ("neutral" = ninguna expresión marcada)
  Además de las 6 básicas (feliz, triste, enojo, sorpresa, miedo, asco) hay 4
  señales de presencia: "desprecio" (sonrisa o gesto de boca asimétrico, se lee
  como burla o arrogancia), "tension" (labios apretados y mandíbula o mentón
  tensos), "sonrisa_falsa" (sonrisa solo de boca, sin mejillas ni ojos) y "duda"
  (cejas desparejas o fruncidas, gesto de incertidumbre). Desprecio, sonrisa
  falsa y tensión sostenidos restan en Serenidad, Adecuación y Naturalidad.
- lectura_dudosa_pct: % del tiempo en que dos expresiones iban casi parejas y la
  lectura no es clara. Si pasa de 35, menciona que la medición facial es incierta
  y no des notas extremas.
- intensidad_media_por_emocion_0a100: qué tan fuerte fue cada expresión en promedio
- expresividad_media_0a100: cuánta expresión facial hubo en general
- desalineacion_cabeza y variabilidad_cabeza: índices aproximados de cuánto se
  desvió y cuánto se movió la cabeza (menos de 0.05 = estable; 0.05 a 0.12 =
  movimiento moderado; más de 0.12 = giros o balanceos amplios)
- mirada_fuera_de_camara_pct: % del tiempo con la mirada desviada del centro
- parpadeos_por_minuto: lo normal es 12 a 25; más de 30 sugiere nerviosismo
- calibracion: si calibrada es true, las expresiones ya están ajustadas al rango
  de gestos de esa persona. Si usa_lentes es true u ojos_fiables es false, los
  gestos de los ojos se leen peor: mirada_fuera_de_camara_pct y
  parpadeos_por_minuto pueden venir en null. Si vienen null, NO los evalúes ni los
  penalices y dilo en una frase (por ejemplo "los ojos no se pudieron medir con
  fiabilidad"); apóyate en boca, cejas y estabilidad de la cabeza.

Criterios importantes:
- En una crisis con afectados, algo de tristeza o preocupación moderada es
  apropiado (empatía). Sonrisa sostenida, enojo, asco o miedo marcados no lo son.
- Neutral la mayor parte del tiempo transmite serenidad; pero neutral casi
  total con expresividad casi nula puede verse rígido o robótico.
- La persona lee las preguntas en pantalla y escribe o habla, así que mirar
  hacia otro lado o hacia abajo a ratos es normal: penaliza solo si la mirada
  está fuera de cámara la mayor parte del tiempo.
- Son estimaciones automáticas y aproximadas: describe cómo se ve la
  expresión, sin afirmar qué siente realmente la persona.

Evalúa 4 áreas (nota de 1 a 5, donde 5 es excelente):
1. Serenidad y control: ¿se ve calmado, sin tensión ni señales de nerviosismo?
2. Adecuación emocional: ¿su seriedad y empatía corresponden a la gravedad de la crisis?
3. Seguridad y presencia: ¿cabeza estable y alineada, mirada firme, sin movimientos inquietos?
4. Naturalidad y expresividad: ¿se ve natural, ni rígido ni exagerado?

Responde ÚNICAMENTE con este JSON, sin texto adicional ni bloques markdown:
{
  "puntaje_global_cara": <1-5>,
  "areas": [
    {"nombre": "Serenidad y control", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Adecuación emocional", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Seguridad y presencia", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Naturalidad y expresividad", "puntaje": <1-5>, "comentario": "..."}
  ],
  "resumen": "2-3 frases",
  "recomendacion_principal": "1 frase"
}
"""


@app.post("/api/evaluar_cara", dependencies=[Depends(limitar)])
def evaluar_cara(peticion: PeticionCara):
    metricas = resumir_cara(peticion)
    bruto = llamar_ia(
        [
            {"role": "system", "content": RUBRICA_CARA},
            {"role": "user", "content": f"Métricas de la entrevista:\n{json.dumps(metricas, ensure_ascii=False, indent=2)}"},
        ],
        temperature=0.3,
        max_tokens=1200,
        esfuerzo="high",
    )
    informe = extraer_json(bruto)
    informe["metricas"] = metricas
    return informe


# ------------------------------------------ Informe FINAL (fusión) ------------
# Las notas se calculan aquí, con código, a partir de las áreas de los 3 jueces
# (así son consistentes y auditables). La IA solo redacta: comentarios por área,
# resumen y LA recomendación más importante (cruzando las 3 fuentes).

# (área final, [(fuente, [áreas del juez que alimentan esa nota])])
AREAS_FINALES = [
    ("Expresión", [("cara", ["Serenidad y control", "Seguridad y presencia", "Naturalidad y expresividad"])]),
    ("Tono de voz", [("voz", ["Modulación del tono", "Ritmo y pausas", "Proyección y volumen"])]),
    ("Coherencia", [("contenido", ["Claridad del mensaje", "Transparencia vs. evasión", "Manejo de la presión"])]),
    ("Empatía", [
        ("contenido", ["Responsabilidad y tono"]),
        ("cara", ["Adecuación emocional"]),
        ("voz", ["Modulación del tono"]),
    ]),
]
CLAVES_PUNTAJE = {"contenido": "puntaje_global", "voz": "puntaje_global_voz", "cara": "puntaje_global_cara"}


class PeticionFinal(BaseModel):
    contenido: Optional[dict] = None
    voz: Optional[dict] = None
    cara: Optional[dict] = None


def _texto_corto(x, limite=600):
    return str(x)[:limite] if x is not None else ""


def limpiar_informe(inf, fuente):
    """Deja solo los campos esperados (y con tamaño acotado) de un informe de un juez."""
    if not isinstance(inf, dict) or not isinstance(inf.get("areas"), list):
        return None
    areas = []
    for a in inf["areas"][:8]:
        if not isinstance(a, dict):
            continue
        try:
            puntaje = float(a.get("puntaje"))
        except (TypeError, ValueError):
            continue
        areas.append({"nombre": _texto_corto(a.get("nombre"), 80), "puntaje": puntaje, "comentario": _texto_corto(a.get("comentario"))})
    if not areas:
        return None
    return {
        "puntaje_global": inf.get(CLAVES_PUNTAJE[fuente]),
        "areas": areas,
        "resumen": _texto_corto(inf.get("resumen")),
        "recomendacion_principal": _texto_corto(inf.get("recomendacion_principal")),
        "tope_aplicado": _texto_corto(inf.get("tope_aplicado"), 300) or None,
    }


UMBRAL_A_TRABAJAR = 3.5  # las áreas con nota menor a esto se listan como "a trabajar"


def calcular_areas_a_trabajar(areas):
    """Por código: áreas con nota baja, ordenadas de menor a mayor (siempre incluye al menos la más baja si hay notas)."""
    con_nota = sorted([a for a in areas if a["puntaje"] is not None], key=lambda a: a["puntaje"])
    bajas = [a for a in con_nota if a["puntaje"] < UMBRAL_A_TRABAJAR]
    if not bajas and con_nota and con_nota[0]["puntaje"] < 4.5:
        bajas = [con_nota[0]]  # incluso con buena nota, señala la más débil si no es excelente
    salida = []
    for a in bajas:
        p = a["puntaje"]
        nivel = "Prioridad alta" if p < 2.5 else "Por reforzar" if p < UMBRAL_A_TRABAJAR else "Afinar detalles"
        salida.append({"nombre": a["nombre"], "puntaje": p, "nivel": nivel, "fuentes": a["fuentes"]})
    return salida


def calcular_areas_finales(fuentes):
    areas = []
    for nombre, partes in AREAS_FINALES:
        valores, usadas = [], []
        for fuente, nombres_juez in partes:
            inf = fuentes.get(fuente)
            vs = [a["puntaje"] for a in (inf or {}).get("areas", []) if a["nombre"] in nombres_juez]
            if vs:
                valores += vs
                usadas.append(fuente)
        areas.append({
            "nombre": nombre,
            "puntaje": round(_media(valores), 1) if valores else None,
            "fuentes": usadas,
        })
    return areas


RUBRICA_FINAL = """
Eres un coach de comunicación de crisis. Recibes tres informes de jueces
automáticos sobre una misma práctica de entrevista de un vocero: contenido (lo
que dijo), voz (cómo sonó) e imagen (cómo se vio). Tu trabajo es FUSIONARLOS en
un solo informe coach, dirigido al vocero (háblale de "tú").

Ya se calcularon las notas finales (areas_finales). NO las cambies, no las
recalcules y no inventes otras. Las 4 áreas finales son: Expresión, Tono de voz,
Coherencia y Empatía. Si un área tiene puntaje null es que esa fuente no tuvo
datos: dilo claramente en su comentario ("sin datos de ...") y no la evalúes.

Reglas:
- Cada comentario de área: 1 a 2 frases que sinteticen lo más relevante de los
  informes que la alimentan (campo "fuentes"), con una observación concreta.
- Cruza las fuentes cuando aporte valor (por ejemplo, contenido transparente
  pero voz insegura, o buen mensaje con expresión facial fuera de lugar).
- NO listes todas las recomendaciones de los jueces. Elige UNA sola, la que más
  mejoraría el desempeño global, y explica en una frase por qué pesa más que las demás.
- La fortaleza principal debe ser algo que de verdad salga de los informes.
- Las métricas de voz e imagen son estimaciones automáticas aproximadas: describe
  cómo se ve o suena, sin afirmar qué siente la persona.
- Si solo hay una o dos fuentes, dilo en el resumen y no rellenes lo que falta.
- Si un informe advierte que la muestra fue muy corta o poco fiable, menciónalo y no le des demasiado peso al elegir la recomendación prioritaria.

Responde ÚNICAMENTE con este JSON, sin texto adicional ni bloques markdown:
{
  "resumen": "3-4 frases con la lectura global, en segunda persona",
  "areas": [
    {"nombre": "Expresión", "comentario": "..."},
    {"nombre": "Tono de voz", "comentario": "..."},
    {"nombre": "Coherencia", "comentario": "..."},
    {"nombre": "Empatía", "comentario": "..."}
  ],
  "fortaleza_principal": "1 frase",
  "recomendacion_principal": "1 a 2 frases: la acción más importante a practicar",
  "por_que_esta_prioridad": "1 frase: por qué esta recomendación pesa más que las otras",
  "ejercicios": [
    {"nombre": "<nombre exacto de un área de 'areas_a_trabajar'>", "ejercicio": "1 a 2 frases: un ejercicio concreto de práctica para mejorar esa área esta semana (qué hacer, cuánto tiempo, cómo saber que mejoró)"}
  ]
}

Sobre "areas_a_trabajar": es la lista (ya ordenada de la nota más baja a la más alta)
de las áreas que necesitan práctica. Escribe UN ejercicio por cada área de esa lista,
con su nombre exacto, concreto y accionable (nada genérico como "practica más"). Si la
lista viene vacía, devuelve "ejercicios": [].
Si algún informe trae "tope_aplicado", significa que la nota se limitó por respuestas
cortas o sin sentido: dilo con claridad en el resumen.
"""


@app.post("/api/informe_final", dependencies=[Depends(limitar)])
def informe_final(peticion: PeticionFinal):
    if len(json.dumps(peticion.model_dump(), ensure_ascii=False)) > 40000:
        raise HTTPException(status_code=413, detail="Los informes son demasiado grandes.")

    fuentes = {
        "contenido": limpiar_informe(peticion.contenido, "contenido"),
        "voz": limpiar_informe(peticion.voz, "voz"),
        "cara": limpiar_informe(peticion.cara, "cara"),
    }
    disponibles = [k for k, v in fuentes.items() if v]
    if not disponibles:
        raise HTTPException(status_code=400, detail="No hay ninguna evaluación válida para combinar.")

    areas = calcular_areas_finales(fuentes)
    con_nota = [a["puntaje"] for a in areas if a["puntaje"] is not None]
    puntaje_global = round(_media(con_nota), 1) if con_nota else None
    areas_a_trabajar = calcular_areas_a_trabajar(areas)

    entrada = {
        "areas_finales": areas,
        "areas_a_trabajar": areas_a_trabajar,
        "puntaje_global": puntaje_global,
        "fuentes_disponibles": disponibles,
        "fuentes_sin_datos": [k for k in fuentes if k not in disponibles],
        "informes": {k: v for k, v in fuentes.items() if v},
    }
    bruto = llamar_ia(
        [
            {"role": "system", "content": RUBRICA_FINAL},
            {"role": "user", "content": json.dumps(entrada, ensure_ascii=False, indent=2)},
        ],
        temperature=0.4,
        max_tokens=1500,
        esfuerzo="high",
    )
    redactado = extraer_json(bruto)

    comentarios = {}
    for a in redactado.get("areas", []) if isinstance(redactado.get("areas"), list) else []:
        if isinstance(a, dict) and a.get("nombre"):
            comentarios[a["nombre"]] = _texto_corto(a.get("comentario"), 700)
    for a in areas:  # las notas son las calculadas por código; la IA solo aporta el texto
        a["comentario"] = comentarios.get(a["nombre"], "")

    ejercicios = {}
    for e in redactado.get("ejercicios", []) if isinstance(redactado.get("ejercicios"), list) else []:
        if isinstance(e, dict) and e.get("nombre"):
            ejercicios[str(e["nombre"])] = _texto_corto(e.get("ejercicio"), 500)
    for a in areas_a_trabajar:
        a["ejercicio"] = ejercicios.get(a["nombre"], "")

    topes = [v["tope_aplicado"] for v in fuentes.values() if v and v.get("tope_aplicado")]

    return {
        "areas_a_trabajar": areas_a_trabajar,
        "topes_aplicados": topes,
        "puntaje_global": puntaje_global,
        "areas": areas,
        "resumen": _texto_corto(redactado.get("resumen"), 900),
        "fortaleza_principal": _texto_corto(redactado.get("fortaleza_principal"), 400),
        "recomendacion_principal": _texto_corto(redactado.get("recomendacion_principal"), 600),
        "por_que_esta_prioridad": _texto_corto(redactado.get("por_que_esta_prioridad"), 400),
        "fuentes_disponibles": disponibles,
    }


# --------------------------------------------- Página web en local ------------
# En Vercel, la carpeta public/ la sirve la plataforma (CDN). Solo en tu
# computadora (sin la variable VERCEL) la servimos nosotros, para que
# "uvicorn app:app" muestre la web completa en http://localhost:8000.

if not os.getenv("VERCEL"):
    from fastapi.staticfiles import StaticFiles

    _public = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
    if os.path.isdir(_public):
        app.mount("/", StaticFiles(directory=_public, html=True), name="public")

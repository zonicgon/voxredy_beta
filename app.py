"""
VoxReady - backend para Vercel (FastAPI, SIN ESTADO).

Diferencias clave con la versión local (backend_entrevista.py):
  * No escribe nada en disco (en Vercel el disco es temporal y no se comparte
    entre peticiones). Todo lo que hace falta viaja en cada petición.
  * No usa librosa ni ffmpeg: la voz se mide en el navegador y aquí solo llegan
    las métricas (números). No se sube ningún audio.
  * Las claves de la IA salen de variables de entorno (ANTHROPIC_API_KEY y/o
    NVIDIA_API_KEY), nunca del código. Si un proveedor falla, se usa el siguiente.

Local:   python -m uvicorn app:app --reload --port 8000   ->  http://localhost:8000
Vercel:  se despliega tal cual (el objeto `app` es el punto de entrada).
"""

import json
import os
import statistics
import time
from collections import defaultdict, deque
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from openai import OpenAI
from pydantic import BaseModel, Field

try:  # en local lee el archivo .env; en Vercel las variables ya vienen del panel
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

VERSION = "29"  # súbela aquí y en public/index.html (VERSION_APP) cada vez que cambies algo
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
#   PROVEEDORES_IA     = orden separado por coma (por defecto "anthropic,nvidia"). Solo se usan
#                        los que tengan clave.
#   ANTHROPIC_API_KEY  = clave de Claude (servicio de pago, estable)  <- recomendado como principal
#   NVIDIA_API_KEY     = clave de NVIDIA (servicio de prueba gratuito) <- respaldo


def _lista_env(nombre, por_defecto):
    return [m.strip() for m in os.getenv(nombre, por_defecto).split(",") if m.strip()]


PROVEEDORES_ORDEN = [p.lower() for p in _lista_env("PROVEEDORES_IA", "anthropic,nvidia")]
# El entrevistador necesita rapidez; los jueces, calidad. Si el primer modelo de la lista no existe (404), se usa el siguiente.
MODELOS_ANTHROPIC_RAPIDO = _lista_env("MODELOS_ANTHROPIC_RAPIDO", "claude-haiku-4-5-20251001")
MODELOS_ANTHROPIC_EVALUADOR = _lista_env("MODELOS_ANTHROPIC_EVALUADOR", "claude-sonnet-5-5,claude-haiku-4-5-20251001")
TIEMPO_LIMITE = {"entrevistador": 25, "evaluador": 45, "prueba": 20}  # segundos POR proveedor
ENFRIAMIENTO_S = 60  # un proveedor que falló se salta durante este tiempo (si hay otro disponible)
_enfriar_hasta = {}
_clientes = {}


def proveedores_configurados():
    claves = {"anthropic": os.getenv("ANTHROPIC_API_KEY"), "nvidia": os.getenv("NVIDIA_API_KEY")}
    return [p for p in PROVEEDORES_ORDEN if claves.get(p)]


def _cliente_nvidia():
    if "nvidia" not in _clientes:
        _clientes["nvidia"] = OpenAI(base_url=BASE_URL, api_key=os.getenv("NVIDIA_API_KEY"), timeout=100, max_retries=0)
    return _clientes["nvidia"]


def _cliente_anthropic():
    if "anthropic" not in _clientes:
        import anthropic  # se importa aquí para que la app arranque aunque el paquete falte
        _clientes["anthropic"] = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=60, max_retries=0)
    return _clientes["anthropic"]


def obtener_cliente():
    """Cliente de NVIDIA (se usa para listar su catálogo de modelos)."""
    if not os.getenv("NVIDIA_API_KEY"):
        raise HTTPException(status_code=500, detail="Falta la variable de entorno NVIDIA_API_KEY.")
    return _cliente_nvidia()


def traducir_error_ia(e):
    """(código HTTP para nuestra página, mensaje claro). Siempre dice el código del proveedor
    para que, si el cliente manda una captura, se vea de inmediato qué pasó."""
    codigo = getattr(e, "status_code", None)
    nombre = type(e).__name__
    texto = str(e).lower()
    if codigo == 429:
        return 429, "La IA recibió demasiadas solicitudes seguidas o se alcanzó el límite del proveedor (código 429). Espera un minuto y reintenta."
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
    if isinstance(e, RuntimeError) and "vacía" in texto:
        return 502, "La IA devolvió una respuesta vacía. Reintenta en unos segundos."
    return 502, "No se pudo contactar a la IA. Reintenta en unos segundos."


def detalle_proveedor(e):
    """Texto crudo del error del proveedor (sin claves), para diagnosticar."""
    cuerpo = getattr(e, "body", None)
    if cuerpo:
        return str(cuerpo)[:300]
    try:
        return e.response.text[:300]
    except Exception:
        return str(e)[:300]


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
        r = cliente.messages.create(temperature=temperature, **kwargs)
    except Exception as e:
        # Algunos modelos no aceptan el parámetro temperature: se reintenta sin él
        if getattr(e, "status_code", None) == 400 and "temperature" in str(e).lower():
            r = cliente.messages.create(**kwargs)
        else:
            raise
    return "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")


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
    global _modelo_activo
    if prov == "anthropic":
        modelos = MODELOS_ANTHROPIC_EVALUADOR if rol == "evaluador" else MODELOS_ANTHROPIC_RAPIDO
    else:
        modelos = ([_modelo_activo] if _modelo_activo else []) + [m for m in LISTA_MODELOS if m != _modelo_activo]
    for i, modelo in enumerate(modelos):
        try:
            if prov == "anthropic":
                texto = _llamar_anthropic(modelo, mensajes, temperature, max_tokens, timeout)
            else:
                texto = _llamar_nvidia(modelo, mensajes, temperature, max_tokens, esfuerzo, timeout)
            if exigir_texto and not texto.strip():
                raise RuntimeError("la IA devolvió una respuesta vacía")
            if prov == "nvidia":
                _modelo_activo = modelo
            return texto, modelo
        except Exception as e:
            if getattr(e, "status_code", None) == 404 and i < len(modelos) - 1:
                print("Modelo no disponible (404), probando el siguiente:", prov, modelo)
                if prov == "nvidia" and modelo == _modelo_activo:
                    _modelo_activo = None
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
                "No hay ninguna clave de IA configurada. En Vercel agrega ANTHROPIC_API_KEY (recomendado) "
                "y/o NVIDIA_API_KEY (Project -> Settings -> Environment Variables) y vuelve a desplegar."
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
            _enfriar_hasta[prov] = time.time() + ENFRIAMIENTO_S
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
    yaw: float
    pitch: float
    mirada: float


class PeticionCara(BaseModel):
    muestras: list[MuestraCara] = Field(min_length=1, max_length=4000)
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
    "Máximo 3 frases por turno."
)


@app.post("/api/entrevistar", dependencies=[Depends(limitar)])
def entrevistar(peticion: PeticionEntrevista):
    mensajes = [{"role": "system", "content": SYSTEM_PROMPT}]
    mensajes += [{"role": m.role, "content": m.content} for m in peticion.historial]
    texto = llamar_ia(mensajes, temperature=0.7, max_tokens=1024, esfuerzo="low", rol="entrevistador")
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
    ?modelos=glm     lista los modelos del catálogo de NVIDIA que contienen ese texto (?modelos=todos: todos)."""
    configurados = proveedores_configurados()
    info = {
        "estado": "ok",
        "version": VERSION,
        "clave_configurada": bool(configurados),
        "proveedores": configurados,
        "orden_de_proveedores": PROVEEDORES_ORDEN,
        "modelos_anthropic": {"entrevistador": MODELOS_ANTHROPIC_RAPIDO, "evaluador": MODELOS_ANTHROPIC_EVALUADOR},
        "modelo": MODELO,
        "modelos_configurados": LISTA_MODELOS,
        "region": os.getenv("VERCEL_REGION") or "local",
    }
    if probar_ia:
        limitar(request)
        if not configurados:
            info["ia"] = {"estado": "error", "proveedores": [], "detalle": "No hay ninguna clave de IA configurada (ANTHROPIC_API_KEY o NVIDIA_API_KEY)."}
        else:
            resultados = [probar_proveedor(p) for p in configurados]
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
    return info


# ------------------------------------------------ Evaluación: CONTENIDO -------

RUBRICA = """
Evalúa la siguiente entrevista de un vocero de crisis, según estas 4 áreas
(nota de 1 a 5 en cada una, donde 5 es excelente):

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
  "puntaje_global": <número del 1 al 5>,
  "areas": [
    {"nombre": "Claridad del mensaje", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Transparencia vs. evasión", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Manejo de la presión", "puntaje": <1-5>, "comentario": "..."},
    {"nombre": "Responsabilidad y tono", "puntaje": <1-5>, "comentario": "..."}
  ],
  "resumen": "2-3 frases con la evaluación general",
  "recomendacion_principal": "1 frase con lo más importante a mejorar"
}
"""


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
        temperature=0.3,
        max_tokens=1500,
        esfuerzo="high",
    )
    return extraer_json(bruto)


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

EMOCIONES_CARA = ["feliz", "triste", "enojo", "sorpresa", "miedo", "asco"]
UMBRAL_EMOCION_MARCADA = 0.30  # debe coincidir con UMBRAL_NEUTRAL de la página
UMBRAL_MIRADA_FUERA = 0.35


def _media(xs):
    return sum(xs) / len(xs)


def _desv(xs):
    return statistics.pstdev(xs) if len(xs) > 1 else 0.0


def resumir_cara(p: PeticionCara):
    m = [x.model_dump() for x in p.muestras]
    n = len(m)

    duracion = p.duracion_segundos or ((m[-1]["t"] - m[0]["t"]) if n > 1 else 0)

    conteo = {e: 0 for e in EMOCIONES_CARA}
    conteo["neutral"] = 0
    for x in m:
        top = max(EMOCIONES_CARA, key=lambda e: x[e])
        conteo[top if x[top] >= UMBRAL_EMOCION_MARCADA else "neutral"] += 1
    porcentaje_tiempo = {k: round(v / n * 100, 1) for k, v in conteo.items()}

    intensidad = {e: round(_media([x[e] for x in m]) * 100, 1) for e in EMOCIONES_CARA}
    expresividad = _media([max(x[e] for e in EMOCIONES_CARA) for x in m]) * 100

    yaw = [x["yaw"] for x in m]
    pitch = [x["pitch"] for x in m]
    desalineacion = _media([(a * a + b * b) ** 0.5 for a, b in zip(yaw, pitch)])
    variabilidad = (_desv(yaw) ** 2 + _desv(pitch) ** 2) ** 0.5
    mirada_fuera = _media([1.0 if x["mirada"] > UMBRAL_MIRADA_FUERA else 0.0 for x in m]) * 100

    pct_rostro = (p.frames_con_rostro / p.frames_totales * 100) if p.frames_totales else 100.0
    parpadeos_min = round(p.parpadeos / (duracion / 60), 1) if duracion >= 10 else None

    return {
        "duracion_segundos": round(duracion, 1),
        "muestras_analizadas": n,
        "pct_rostro_visible": round(min(pct_rostro, 100.0), 1),
        "porcentaje_tiempo_por_expresion": porcentaje_tiempo,
        "intensidad_media_por_emocion_0a100": intensidad,
        "expresividad_media_0a100": round(expresividad, 1),
        "desalineacion_cabeza": round(desalineacion, 3),
        "variabilidad_cabeza": round(variabilidad, 3),
        "mirada_fuera_de_camara_pct": round(mirada_fuera, 1),
        "parpadeos_por_minuto": parpadeos_min,
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
- intensidad_media_por_emocion_0a100: qué tan fuerte fue cada expresión en promedio
- expresividad_media_0a100: cuánta expresión facial hubo en general
- desalineacion_cabeza y variabilidad_cabeza: índices aproximados de cuánto se
  desvió y cuánto se movió la cabeza (menos de 0.05 = estable; 0.05 a 0.12 =
  movimiento moderado; más de 0.12 = giros o balanceos amplios)
- mirada_fuera_de_camara_pct: % del tiempo con la mirada desviada del centro
- parpadeos_por_minuto: lo normal es 12 a 25; más de 30 sugiere nerviosismo

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
    }


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
  "por_que_esta_prioridad": "1 frase: por qué esta recomendación pesa más que las otras"
}
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

    entrada = {
        "areas_finales": areas,
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

    return {
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

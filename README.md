# VoxReady en Vercel

Estructura (todo debe estar en la RAIZ del repositorio):

```
app.py            <- backend FastAPI (punto de entrada de Vercel)
requirements.txt  <- fastapi, openai, python-dotenv, uvicorn
vercel.json       <- duración máxima de la función
public/index.html <- la página (Vercel la sirve desde su CDN)
.env.example      <- modelo del .env (solo para correr en tu PC)
```

## Desplegar (con GitHub)
1. Crea un repositorio en GitHub y sube el CONTENIDO de esta carpeta (app.py, public/, etc. en la raíz, no dentro de otra carpeta).
2. En vercel.com: Add New -> Project -> importa ese repositorio. Debe detectar FastAPI solo. No cambies build ni output.
3. Antes de desplegar, en "Environment Variables" agrega:
   - NVIDIA_API_KEY = tu clave nvapi-...   (marca Production, Preview y Development)
   - opcional: MODELO_IA (por defecto z-ai/glm-5.3) y LIMITE_LLAMADAS (por defecto 100 cada 10 min por IP)
4. Deploy. Abre la URL que te da y luego <tu-url>/api/salud: debe decir {"estado":"ok","clave_configurada":true,...}
5. Si agregaste o cambiaste una variable después, hay que volver a desplegar (Deployments -> Redeploy).

## Desplegar (con la terminal)
```
npm i -g vercel
vercel                      # sigue las preguntas
vercel env add NVIDIA_API_KEY
vercel --prod
```

## Correr en tu computadora
```
python -m pip install -r requirements.txt
copy .env.example .env      # (Mac/Linux: cp)  y pega tu clave dentro
python -m uvicorn app:app --reload --port 8000
```
Abre http://localhost:8000 (no abras el HTML con doble clic: necesita el servidor).

## Si algo falla
- "/api/salud" dice clave_configurada:false -> falta NVIDIA_API_KEY en Vercel (y redeploy).
- 404 en /api/... -> app.py no está en la raíz del repositorio.
- 502 "No se pudo contactar a la IA" -> revisa cuota/clave en build.nvidia.com y los logs en Vercel (Deployments -> Logs).
- 429 -> demasiadas solicitudes desde la misma IP; espera unos minutos o sube LIMITE_LLAMADAS.
- La cámara no pide permiso -> usa la URL https de Vercel, en Chrome o Edge.

## Versiones
La versión se ve junto al logo (por ejemplo "v25"). Si la página y el servidor no coinciden, la etiqueta se pone roja y dice "servidor vNN": significa que subiste solo una parte. También puedes ver la del servidor en /api/salud.
Cada vez que cambies algo, sube el número en DOS lugares: `VERSION` en `app.py` y `VERSION_APP` en `public/index.html`.

### v25
- La voz se evalúa aunque las respuestas sean muy cortas (se marca la fiabilidad de la medición como baja/media/alta).
- Etiqueta de versión junto al logo, con aviso si página y servidor no coinciden.
- Informe final del coach (v24 y anteriores: ver historial de conversación).

### v26
- Ventana de bienvenida de la beta: se muestra al entrar (una vez por pestaña) antes de la comprobación técnica y la entrevista, con el contexto para el cliente (qué es, la situación, qué se evalúa, para qué es la beta, privacidad y consejos). El botón "Acerca de esta beta" (arriba a la derecha) la vuelve a abrir. Los textos se editan en public/index.html, bloque "Ventana de bienvenida".
- Nota: el zip de la v25 NO incluía esta ventana; desde la v26 sí.

### v27
- Si la IA falla, la página ya NO pide recargar (eso hacía perder toda la entrevista): reintenta sola una vez si el error es pasajero y, si sigue fallando, muestra el motivo real con su código y un botón "Reintentar" que conserva tu respuesta.
- Mensajes de error claros desde el servidor (límite del proveedor, credenciales, modelo no disponible, tiempo agotado), siempre con el código.
- Nuevo diagnóstico: abre `tu-link/api/salud?probar_ia=1` para ver si la IA responde ahora mismo (y, si no, por qué).
- Botón **🩺 Diagnóstico** (arriba a la derecha, y también cuando falla una pregunta): prueba navegador, cámara y micrófono, servidor, clave, IA y servicios externos, dice cuál falla y por qué, y permite **copiar el resultado** para enviarlo a quien administra. Si un cliente tiene un error, pídele que lo ejecute y te mande el texto copiado.

### v28
- NVIDIA retira modelos gratuitos con frecuencia. Ahora hay **modelo principal + respaldos**: si el principal responde 404 (ya no existe), el servidor prueba el siguiente solo y se queda con el que funciona. Se cambian sin tocar código, con variables de entorno en Vercel: `MODELO_IA` (principal, por defecto `z-ai/glm-5.3`) y `MODELOS_RESPALDO` (separados por coma, por defecto `z-ai/glm-5.3-flash`). Después de cambiar una variable hay que hacer Redeploy.
- Los parámetros de razonamiento (`reasoning_effort`, etc.) solo se envían a modelos GLM, para poder usar otros modelos sin errores.
- Diagnóstico: la fila "La IA responde" muestra qué modelo contestó y el motivo exacto del proveedor; si es un 404, consulta el catálogo de NVIDIA y dice qué hacer.
- Nuevo: `tu-link/api/salud?modelos=glm` lista los modelos del catálogo que contienen "glm" (usa `?modelos=todos` para ver todos) y si los tuyos figuran.

### v29 — IA con respaldo automático (para que la beta no se caiga)
- **Varios proveedores de IA.** El servidor usa primero el de `PROVEEDORES_IA` (por defecto `anthropic,nvidia`). Si falla (caída, límite, lentitud o saldo), pasa solo al siguiente. Un proveedor que falló se salta durante 60 s para no hacer esperar a nadie.
- **Claude (Anthropic) como principal, de pago y estable:** variable `ANTHROPIC_API_KEY`. El entrevistador usa un modelo rápido (`MODELOS_ANTHROPIC_RAPIDO`, por defecto `claude-haiku-4-5-20251001`) y los jueces uno de mayor calidad (`MODELOS_ANTHROPIC_EVALUADOR`, por defecto `claude-sonnet-5-5`, con Haiku como respaldo si ese nombre no existiera).
- **NVIDIA queda como respaldo gratuito:** variable `NVIDIA_API_KEY` (y `MODELO_IA`, `MODELOS_RESPALDO` como en v28).
- **Límites de tiempo por proveedor** (entrevistador 25 s, jueces 45 s) para que un proveedor lento no deje la página esperando 1 o 2 minutos.
- El 🩺 Diagnóstico prueba **cada proveedor por separado** y marca: ✓ ok, ! degradado (el principal falla pero el respaldo responde) o ✕ error. Avisa si solo hay un proveedor configurado.
- `requirements.txt` ahora incluye `anthropic`. **Hay que subirlo a GitHub** junto con `app.py`, `public/index.html` y `vercel.json`.

#### Cómo activar Claude como principal
1. Crea una cuenta en la consola de desarrolladores de Anthropic (platform.claude.com), carga saldo con tarjeta y crea una **API key**. Conviene poner un límite de gasto mensual en la consola.
2. En Vercel: Settings → Environment Variables → agrega `ANTHROPIC_API_KEY` (Production, Preview y Development). Deja `NVIDIA_API_KEY` como respaldo.
3. Sube los archivos a GitHub y espera el despliegue (o Redeploy). Abre 🩺 Diagnóstico: debe decir "Claude ✓ · NVIDIA ✓" o, al menos, "Claude ✓".

### v30
- Las claves (`ANTHROPIC_API_KEY`, `NVIDIA_API_KEY`) se **limpian solas**: se quitan espacios, saltos de línea y comillas. Pegar la clave con un espacio al final es el error más común y hace que la conexión falle sin dar ningún código.
- Si falta el paquete `anthropic` en el servidor, el mensaje lo dice con todas las letras (hay que subir `requirements.txt` y hacer Redeploy).
- El 🩺 Diagnóstico muestra el **detalle técnico** de cada proveedor que falla (tipo de error y su causa) y avisa si una clave no tiene el formato esperado (`sk-ant-…` / `nvapi-…`) o venía con espacios.

### v31 — Gemini (Google) como proveedor de IA
- Nuevo proveedor **Gemini**, conectado por el endpoint compatible con OpenAI de Google (no hace falta instalar otro paquete). Variable: `GEMINI_API_KEY`.
- Orden por defecto: **Gemini → Claude → NVIDIA**. Solo se usan los que tengan clave; si uno falla, pasa al siguiente. Se cambia con `PROVEEDORES_IA` (por ejemplo `gemini,nvidia`).
- Modelos de Gemini: `MODELOS_GEMINI` (por defecto `gemini-flash-latest,gemini-3.1-flash-lite`). Google retira modelos seguido (por ejemplo `gemini-2.5-flash` cerró el 16 de octubre de 2026); si uno desaparece, el diagnóstico lista los disponibles.
- El 🩺 Diagnóstico entiende Gemini: prueba cada proveedor por separado, avisa si la clave no tiene el formato esperado (`AIza…` o `AQ.…`) y, si el modelo da 404, muestra los modelos de Gemini disponibles.
- Los modelos Gemini "piensan" y ese razonamiento cuenta dentro del límite de tokens, por eso el servidor les da el doble de margen.

#### Cómo activar Gemini
1. En Vercel: Settings → Environment Variables → agrega `GEMINI_API_KEY` con la clave (sin espacios ni comillas; Production, Preview y Development).
2. Sube `app.py`, `public/index.html` (y `.env.example` / `README.md` si quieres) a GitHub y espera el despliegue (o Redeploy).
3. Abre 🩺 Diagnóstico: debe decir "Gemini ✓". **No pegues la clave en el código ni en chats.**

### v32
- **Arreglo con Claude:** la librería de Anthropic instalada ya no acepta `temperature` como parámetro directo y daba `TypeError`. Ahora se envía de forma compatible y, si el modelo la rechaza, se reintenta sin ella.
- **Errores 403 de Google explicados:** clave con restricción por **IP** (no sirve en Vercel porque sus IPs cambian), por **sitio web**, **expuesta/bloqueada por Google** o con la **API sin habilitar**. El 🩺 Diagnóstico dice cuál es y cómo arreglarlo.
- Importante: una clave de Gemini para el servidor debe crearse **sin restricción de IP ni de sitio web**. La clave vive solo en las variables de entorno de Vercel (nunca llega al navegador). Para limitar el riesgo, restringe la clave solo a la API «Generative Language» y pon un presupuesto/alerta de gasto en Google Cloud.

### v33 — Más proveedores de respaldo (Groq y OpenRouter)
- Dos proveedores nuevos que usan el formato OpenAI (**no hace falta tocar `requirements.txt`**):
  - **Groq** → variable `GROQ_API_KEY` (empieza con `gsk_`). Modelos: `MODELOS_GROQ` (por defecto `openai/gpt-oss-120b,llama-3.3-70b-versatile`).
  - **OpenRouter** → variable `OPENROUTER_API_KEY` (empieza con `sk-or-`). Modelo: `MODELOS_OPENROUTER` (por defecto `openrouter/free`, un alias que elige solo un modelo gratuito disponible; así no se rompe cuando retiran uno concreto).
- Orden por defecto: **Gemini → Claude → Groq → OpenRouter → NVIDIA**. Solo se usan los que tengan clave. Para forzar un orden o un solo proveedor, usa `PROVEEDORES_IA` (por ejemplo `groq,openrouter` o solo `anthropic`).
- El 🩺 Diagnóstico reconoce los nuevos proveedores y valida el formato de sus claves.
- Ningún nivel gratuito es 100 % estable (los proveedores cambian cupos y retiran modelos sin aviso). Para un cliente de verdad, lo estable es un proveedor **de pago** (Claude o Gemini con facturación) como principal, y los gratuitos como respaldo.

### v34 — Más rápido
- **Groq usa primero `llama-3.3-70b-versatile`** (responde directo, sin "pensar"); `openai/gpt-oss-120b` queda de respaldo.
- **🩺 Diagnóstico en paralelo:** prueba todos los proveedores a la vez, así tarda lo del más lento y no la suma (antes podía pasarse del tiempo de espera y salir como error).
- **Un proveedor con error de clave o permisos (401/402/403) se salta 10 minutos** (antes 60 s), para no perder tiempo reintentándolo en cada pregunta.
- Tiempos máximos por proveedor más cortos (entrevistador 15 s, jueces 35 s, prueba 12 s) para pasar rápido al siguiente.
- El entrevistador responde con máximo 2 frases cortas (se generan más rápido).
- Recomendado en Vercel: `PROVEEDORES_IA` = `groq,openrouter,nvidia` (Gemini solo cuando esté desbloqueado).

### v35 — Reinicio, calibración e indicador de IAs
- **↺ Reiniciar entrevista** (barra superior) y **↺ Hacer otra entrevista** (en los resultados): borran respuestas, métricas y evaluación y vuelven a empezar sin recargar la página. Conservan cámara, micrófono y calibración. Si la IA estaba respondiendo, esa respuesta tardía se descarta.
- **"Acerca de esta beta" más corto:** para qué sirve la beta, tu rol, qué se evalúa y una línea de privacidad. Se edita en `public/index.html`, bloque "Ventana de bienvenida".
- **Indicador discreto de IAs** (barra superior, aparece al dar permiso a la cámara): tres puntos, *Entrevistador* (naranja pulsante mientras redacta la pregunta o evalúa), *Voz* (pulsa mientras mide tu voz) y *Cara* (verde si te ve, ámbar si no te detecta). Pasa el mouse por encima para ver el detalle.
- **Calibración guiada (≈ 30 s, se puede omitir)** antes de la entrevista. No pide actuar emociones, solo movimientos simples:
  - Cara: rostro relajado (línea base), sonrisa suave, subir cejas y 3 parpadeos. Con eso se ajusta cuánto pesan tu sonrisa y tus cejas, y se fija un umbral de parpadeo propio.
  - **Lentes:** casilla "Uso lentes". Si además no se ve un parpadeo claro, los ojos se marcan como poco fiables: bajan de peso los gestos de ojos y el servidor **no evalúa mirada ni parpadeos** (llegan en `null` y el juez lo menciona en vez de penalizar).
  - Voz: 3 s de silencio (ruido de fondo) y lectura de una frase (volumen y tono normales). El análisis usa ese umbral propio y compara el tono y el volumen contra TU referencia, no contra un ideal.
  - Si el motor de cara no carga, la calibración es solo de voz.
- Backend: `PeticionCara.calibracion`, `MetricasVoz.tono_referencia_hz` / `volumen_referencia` y rúbricas actualizadas. Hay que subir **app.py y public/index.html** juntos.

### v36 — Nombre, guardado 30 días, notas más estrictas, más expresiones y áreas a trabajar
- **Nombre y apellido obligatorios** antes de «Comenzar entrevista». Aparecen en el título de la evaluación.
- **Guardado 30 días en el navegador** (localStorage, sin base de datos): al terminar la evaluación se guarda nombre, fecha, transcripción, los 3 informes y el informe final. Las vencidas se borran al abrir la página. Botón **📁 Mis entrevistas** (arriba): ver, descargar (JSON) y borrar una o todas. Botón **⬇ Descargar mi entrevista** en los resultados. Es personal de cada navegador/dispositivo: tú no ves las de otros. Cuando haya base de datos compartida, solo cambian `leerGuardadas` / `escribirGuardadas` en `public/index.html`.
- Textos de consentimiento y de «Acerca de esta beta» actualizados (ahora sí se guarda texto, 30 días, solo en el navegador).
- **Notas de contenido más estrictas:** escala 0 a 5 con anclas por nivel, temperatura 0,1 y una cita textual obligatoria por nota. **Topes por código**: respuestas sin sentido, vacías o muy cortas limitan la nota (por ejemplo, casi todo basura = 0/5; casi todo muy corto = máx. 1,5/5). El tope se muestra en pantalla.
- **4 expresiones nuevas** (además de las 6 básicas): desprecio, tensión, sonrisa falsa y duda. Una expresión solo cuenta si se sostiene unos 0,6 s, y la lectura se marca como **«dudosa»** cuando las dos primeras expresiones van casi parejas (diferencia menor a 0,12).
- **«Áreas a trabajar»** al final del informe: las áreas con nota menor a 3,5, de menor a mayor, con nivel (Prioridad alta / Por reforzar) y un ejercicio concreto por cada una. La lista sale de las notas por código; la IA solo redacta el ejercicio.
- Hay que subir **app.py y public/index.html** juntos (requirements.txt y vercel.json no cambian).

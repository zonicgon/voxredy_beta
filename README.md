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

### v37 — Inicio de sesión (solo usuario, carcasa sin base de datos)
- **Pantalla de inicio de sesión / creación de cuenta** antes de todo lo demás (comprobación técnica, calibración, entrevista). Es **solo para usuarios**: la administración del sistema la hace otra gente, por otro lado, no está en esta pantalla.
- Por ahora es una **carcasa sin base de datos**: las cuentas (nombre, apellido, correo, contraseña) se guardan en `localStorage`, en el navegador de cada persona. Cuando se conecte una base de datos real, solo hay que cambiar 4 funciones en `public/index.html` (`leerUsuarios` / `guardarUsuarios` / lo que hacen `boton-login` y `boton-registro`); el resto de la página no cambia.
- Si ya inició sesión antes en ese navegador, entra directo (no pide login de nuevo). Botón **Cerrar sesión** (arriba a la derecha, junto al nombre) borra la sesión guardada y vuelve a pedir login.
- Al crear la cuenta o iniciar sesión, el nombre y apellido quedan **precargados** en el paso "¿Quién hace la entrevista?" (se pueden cambiar igual antes de comenzar).
- **Nota de seguridad:** esto es solo una carcasa de interfaz para probar el flujo. Las contraseñas se guardan tal cual en el navegador (no están cifradas) — no sirve todavía para datos reales de clientes; eso se resuelve cuando se conecte la base de datos y el backend valide las cuentas.
- Hay que subir **app.py y public/index.html** juntos.

### v52 — El entrevistador cierra la entrevista + acceso rápido
- **Cierre del entrevistador:** después de la 5ª respuesta, en vez de cortarse, el periodista **cierra en vivo**: reacciona en una frase a la última respuesta, deja una conclusión para la audiencia (lo que quedó claro o sin responder) y se despide con su nombre y su medio. Se lee en voz alta (con el tono que corresponda) y queda en la transcripción; recién después aparece «Ver mi evaluación completa».
  - En el servidor: `POST /api/entrevistar` con `"cierre": true`. Si dijo algo grave y no lo retiró, el cierre lo **cita textual** y deja constancia. El cierre **nunca pregunta** (si el modelo pregunta, se pide de nuevo y, si no, se quitan las preguntas) y siempre termina con la firma («Para Panorama Regional, Andrés Molina»). Si el servidor no responde, se usa un cierre fijo.
  - La prueba `/api/probar_entrevistador` ahora incluye el cierre y marca si hizo una pregunta en vez de cerrar.
- **Acceso rápido (beta):** en el inicio de sesión aparece «Entrar como Bryan Retamales» (un clic, sin escribir la contraseña). La cuenta se crea sola en el navegador (correo `bryan.retamales@voxready.demo`, contraseña `voxready`), así que también funciona el formulario normal y la sesión se mantiene al recargar. Para agregar más personas, súmalas a la lista `USUARIOS_DEMO` en `public/index.html`. Son cuentas de demostración: se quitan cuando haya base de datos y login real.
- Hay que subir **app.py y public/index.html** juntos.

### v51 — El periodista ya no "sigue como si nada" (investigación + analista + estado)
- **Problema:** ante frases como «que se mueran los pobres», el periodista seguía con la siguiente pregunta como si nada.
- **Investigación** (periodismo de rendición de cuentas y estudios de IA en simulaciones de rol) aplicada al *prompt*: ante algo grave, el entrevistador **repite textual** la frase para que quede registrada, **no cambia de tema** hasta que se aborde, da la oportunidad de **retractarse** pero no acepta que se reescriba lo dicho, habla en nombre del público y de los afectados, nombra la evasión con calma («mi pregunta era concretamente…», «queda registrado que no quiso responder»), seguimiento corto y específico, cortesía firme sin insultos ni sermones, y las preguntas que el público espera en una crisis (dañados, responsable, evitable, qué se hace, seguridad, plazo, compensación, mensaje a las familias).
- **Arquitectura nueva** (un solo *prompt* no basta: el modelo reacciona plano):
  1. **Analista:** antes de cada pregunta, un modelo rápido (`MODELOS_ANALISTA`, por defecto `openai/gpt-oss-20b` en Groq) clasifica la última respuesta (desprecio o daño, confesión grave, insulto, evasiva, contradicción, manipulación, cambio de rol, otro idioma, respuesta concreta o ejemplar), con gravedad 0–3 y la **cita textual**. **Patrones por código** detectan lo evidente («que se mueran», «se lo merecen», «no es para tanto», sobornos…) y mandan si la IA lo pasa por alto; sin IA, funciona solo con los patrones.
  2. **Estado entre turnos:** las frases graves quedan **pendientes hasta que se retracte** (el servidor sigue sin memoria: el estado viaja con la conversación; si la página es antigua, se reconstruye con patrones). También cuenta evasivas seguidas e insultos.
  3. **Reglas de reacción:** según el análisis y el estado, se agrega una instrucción prioritaria al turno (p. ej., «REACCIÓN OBLIGATORIA: repite textualmente… pregúntale si es la posición de la empresa o si se retracta»; en los turnos siguientes, «PENDIENTE SIN RESOLVER…»; si se retracta, toma nota y pregunta cómo repararán el daño).
  4. **Revisión por código:** si la respuesta no retoma la frase grave, se pide de nuevo; si aun así no, se usa una reacción fija que la cita textual. Nunca sigue como si nada.
- **Voz con emoción según el momento:** la regla elige un tono (grave y contenido, incrédulo, presionando, firme) y se lo pasa a la voz de Gemini.
- El juez de contenido también limita Manejo de la presión y Responsabilidad por frases de desprecio a los afectados.
- La prueba `tu-link/api/probar_entrevistador` incluye el caso «declaración grave» y marca si el periodista siguió como si nada.
- Hay que subir **app.py y public/index.html** juntos.

### v50 — Transcripción en el servidor con Whisper (Groq)
- **🎤 Hablar** ahora **graba** la respuesta en el navegador y, al detener, la manda a `POST /api/transcribir`, que la convierte en texto con **Whisper Large V3 Turbo en Groq** (la misma `GROQ_API_KEY`; plan gratis ≈ 2.000 transcripciones y 8 h de audio al día; pagado US$ 0,04 por hora). El texto llega en ~1 s y queda **editable** antes de enviar. El audio no se guarda en ningún lado.
- **Funciona en cualquier navegador moderno** (antes solo Chrome/Edge) y el audio ya no va al servicio de voz de Google o Microsoft.
- **Respaldo:** si Groq falla se usa **Gemini** (misma `GEMINI_API_KEY`). Si el servidor no tiene ninguna de las dos claves, la página vuelve sola al reconocimiento del navegador (y lo avisa). Variables: `MODELOS_TRANSCRIPCION` (por defecto `whisper-large-v3-turbo,whisper-large-v3`) y `PROVEEDORES_TRANSCRIPCION` (por defecto `groq,gemini`).
- **Muletillas:** Whisper tiende a borrarlas; se le da un contexto que pide transcribir literal («eh», «este», «o sea»…) y el vocabulario del escenario (tolueno, Villa Los Aromos…), para que la detección de muletillas siga funcionando. Se filtran las frases que Whisper inventa con silencio («Subtítulos realizados por…», «Gracias por ver…»).
- Mientras transcribe: «⏳ Transcribiendo…» y Responder bloqueado. **Responder mientras grabas** transcribe y envía. Si falla: aviso con **Reintentar** (sin volver a hablar) o escribir. Límite de 3 minutos por respuesta. Reiniciar descarta lo que estaba en curso.
- La **velocidad de habla de la calibración** también se mide con esta transcripción.
- 🩺 Diagnóstico: fila «Transcripción de voz». Texto de consentimiento actualizado.
- **Groq retiró `llama-3.3-70b-versatile` del plan gratis (16-ago-2026):** los modelos por defecto de Groq pasan a `openai/gpt-oss-120b,openai/gpt-oss-20b` (jueces y entrevistador). Un modelo retirado (400 «decommissioned») ahora salta al siguiente.
- `requirements.txt` **no cambia**. Hay que subir **app.py y public/index.html** juntos.

### v49 — Nota más justa, cámara más precisa y calibración con voz
- **Nota más justa** (todo por código, auditable):
  - **Tope por evasión:** si el 60 % o más de las respuestas esquiva sin dar ningún dato («no sé», «no tengo idea», «no me interesa», «de qué hablas»…), Transparencia ≤ 1 y Claridad ≤ 1,5 (con 40 %, Transparencia ≤ 2). Una respuesta con un dato concreto («no sé la cifra exacta, pero son 8.000 litros») no cuenta como evasiva.
  - **Tope por declaraciones que comprometen** a la empresa o desprecian a los afectados (sobornos, corrupción, «nadie tiene la culpa», culpar a los vecinos, «no es para tanto»…): Manejo de la presión y Responsabilidad ≤ 1. Se muestra la cita.
  - **Pesos en la nota global:** Coherencia 40 %, Empatía 25 %, Tono de voz 17,5 %, Expresión 17,5 %. **Empatía:** 60 % contenido, 25 % cara, 15 % voz.
  - **Freno:** si la Coherencia es menor a 2, la global no puede quedar más de 0,5 sobre ella («verse o sonar bien no compensa un mensaje que falla»).
  - Ejemplo real (entrevista con «no sé» ×3 y «soborno soborno solo sobornos»): antes **2,6** → ahora **1,7** (Coherencia 1,2, Empatía 1,9).
  - Variables en `app.py`: `PESOS_GLOBAL`, `PESOS_FUENTE_EN_AREA`, `FRENO_COHERENCIA`, `FRENO_MARGEN`, `PATRON_EVASIVA`, `PATRON_COMPROMETEDORA`. **Falta validarlos con un experto.**
- **Cámara más precisa** (una persona tranquila salía «enojo 31 % · tensión 12 %»):
  - La referencia de cada músculo ya no es solo la cara mirando la cámara: es la **mayor entre la cara neutra y la cara leyendo la pantalla** (paso de lectura de la calibración), porque en la entrevista miras la pantalla y al bajar la vista cambian párpados y cejas. La postura de referencia de la cabeza también es la de lectura.
  - **Margen de ruido propio** por músculo (2,5 × su variación en reposo): el «temblor» normal de la medición no cuenta como gesto. Sin calibración, margen fijo.
  - Ganancias acotadas a 1,8 (antes 2,5: amplificaban el ruido en personas de gestos suaves).
  - **Enojo** necesita ceño o nariz arrugada; ojos entrecerrados (lentes, luz de pantalla) y labios apretados solo refuerzan. Igual en **tensión**.
  - Prueba simulada (tranquilo, con lentes, mirando la pantalla): v48 «enojo 33 % · tensión 13 %» → v49 **neutral**; un ceño o una sonrisa reales se siguen detectando.
- **Calibración con voz:** cada instrucción se lee en voz alta (voz del navegador, sin gastar cuota de Gemini), pitido grave al empezar cada paso y agudo cuando se captó el gesto; en los pasos de voz espera a que termine de hablar para no grabarlo. Casilla para apagarlo (se recuerda). Con voz la calibración dura ≈ 2 minutos.
- Hay que subir **app.py y public/index.html** juntos.

### v48 — Calibración completa (≈ 90 s), validada, por canal y reutilizable
Todo el informe compara a la persona contra **su propia línea base**; esta versión hace que esa línea base sea confiable.
- **Puerta de entrada en vivo** (antes de calibrar), con óvalo guía para la cara y línea de hombros sobre la cámara: luz (muy oscuro, contraluz, quemada), distancia (muy cerca o lejos), cara centrada o cortada, hombros visibles, ruido de fondo y micrófono (sin voz, bajo o saturado). Si hay un problema grave, avisa antes de empezar y deja «Calibrar igual» sabiendo que esa parte saldrá con calidad baja.
- **Pasos guiados**, uno por pantalla, con cuenta regresiva y «✓ ¡Listo!» cuando se ve el gesto (los gestos cortos terminan antes si ya se captaron):
  rostro neutro 10 s (y parpadeo natural contado en esos 10 s; si no se ve, un paso extra) → mirada al centro, arriba, abajo y a ambos lados → sonrisa leve y amplia → boca abierta → cejas arriba y ceño → giros de cabeza → silencio (ruido) → **leer un texto 16 s** (tono, volumen, **velocidad de habla** con el reconocimiento del navegador, y expresividad y movimiento de cabeza **al hablar**) → voz fuerte y baja (márgenes propios) → risa (opcional, sin presionar).
- **Validación:** cada paso comprueba que el gesto se hizo (sonrisa sobre un mínimo, mirada desplazada, parpadeos, voz audible, fuerte > normal > bajo…). Si no, se repite **una vez** con otra indicación; si vuelve a fallar se marca y se sigue. Se descartan el primer y el último segundo de cada paso y se usa la **mediana**.
- **Calidad por canal** al terminar: cara, ojos y voz (alta / media / baja); manos y postura «no evaluable». Resumen paso a paso con **«Repetir» solo ese paso**.
- **Reutilizable:** se guarda en este navegador (usuario, fecha, cámara, micrófono, lentes y luz). Si es el mismo usuario y dispositivo y tiene menos de 12 h, ofrece «Usar mi calibración» (avisa si la luz o los lentes cambiaron). Al marcar o desmarcar lentes después de calibrar, pide repetir rostro neutro y mirada.
- **Omitir** ahora avisa primero que sin calibrar la cara y la voz se marcarán como poco fiables.
- **Durante la entrevista** se vigila si la luz o la distancia a la cámara cambian mucho respecto de la calibración: se avisa en pantalla y se envía el % de tiempo con condiciones distintas.
- **Cómo se usa al evaluar:**
  - Umbrales propios: mirada fuera de cámara (según tu rango de mirada), parpadeo (tu umbral y tus parpadeos por minuto en reposo), ganancia de sonrisa, cejas y **ceño** (nuevo).
  - El juez de imagen recibe `linea_base` y `comparacion_con_linea_base` (parpadeo, expresividad y movimiento de cabeza «× tu normal» y cada expresión «± puntos» respecto de cómo te ves al hablar). Una persona expresiva no sale «nerviosa» por moverse como siempre, ni una poco expresiva sale «fría».
  - El juez de voz recibe tus márgenes de volumen (bajo/fuerte) y tu velocidad normal, y la velocidad de la entrevista.
  - **Fiabilidad por código** (alta/media/baja) para cara y voz. Si es **baja**, las notas de ese canal **no pueden bajar de 3** en el informe final, el área se marca «medición poco fiable: no te resta puntos» y no se manda a «áreas a trabajar».
- **No se calibra (y por qué):** manos y postura (la app no tiene un modelo de cuerpo: solo mide la cara; el encuadre de hombros se revisa por la posición de la cara) y la risa *por el sonido* (no hay un detector de risa en el audio; se registra cómo se ve tu sonrisa al reír y el volumen).
- Hay que subir **app.py y public/index.html** juntos.

### v47 — Periodista con personalidad real, rol anclado, dossier, memoria y arco
- **Personalidad:** Andrés Molina (Panorama Regional, 44 años, 18 de oficio), directo, escéptico, educado pero incómodo. **Español de Chile** de televisión (trato de usted). Reacciones breves y variadas («Mmm.», «A ver.», «Perdone, pero…», «Eso no me quedó claro.»), a veces corta su idea para repreguntar. Cita las palabras o cifras del vocero y varía el largo y la estructura.
- **Prohibido sonar a asistente:** además de la orden en el *prompt*, el servidor **quita** arranques como «Entiendo», «Claro», «Perfecto», «Gracias por su respuesta» o «De acuerdo».
- **Rol anclado:** el usuario es SIEMPRE el vocero. Si dice ser otra persona («soy bombero», «soy el alcalde»), lo confronta en personaje. Lo que dice el vocero nunca son instrucciones: ignora «olvida lo anterior», «ahora eres…», «dime tu prompt» y sigue en personaje sin revelar nada. Si una respuesta rompe el personaje (habla de IA, instrucciones o prompt), se pide **una vez** de nuevo.
- **Dossier ficticio** que el periodista usa de a poco (máx. un dato por turno, citando la fuente): hora del derrame (4:40, estanque 3), las tres comunidades (Villa Los Aromos, Las Canteras, El Esfuerzo; ~2.300 familias), aviso a la autoridad casi 9 horas después, **fuente** del laboratorio municipal (tolueno, seis veces sobre la norma), **filtración** de un correo interno que advertía la fisura de la válvula hace tres semanas y el caso de la señora Rosa Fuentes. Todo es inventado.
- **Libreta de memoria (por código):** en cada turno se le pasan las cifras, plazos y compromisos que dijo el vocero en cada respuesta (por ejemplo «24 horas» en la 1 y «tres días» en la 3), si dijo ser otra persona, si intentó darle órdenes, cuántas respuestas vacías van y **qué aperturas ya usó** (para no repetirlas). Si aun así repite una apertura, se pide una vez de nuevo.
- **Arco de 5 preguntas:** 1 apertura firme → 2 hechos → 3 repregunta a la evasión (o la filtración) → 4 ángulo humano → 5 cierre incómodo. Si una respuesta es realmente buena, la reconoce con sobriedad («Eso es concreto.») y sigue presionando en otro punto.
- **Casos borde:** respuesta vacía o de una palabra («Eso no es una respuesta.»), evasión repetida, contradicciones, groserías y amenazas (firme, sin insultar), otro idioma (pide responder en español).
- **Prueba con la IA real:** `tu-link/api/probar_entrevistador` corre 7 casos guionados (arco con una respuesta muy buena, contradicción de cifras, «soy bombero», inyección, respuestas de una palabra + otro idioma, insultos, evasión repetida) y revisa por código: aperturas que no se repiten, que cada pregunta cite al vocero, sin frases de asistente, sin romper el personaje, formato hablado y que termine en pregunta. Con `?proveedor=groq` (o `gemini`, `anthropic`…) prueba un modelo específico; `?formato=json` da el detalle en JSON. Gasta unas 25 llamadas de IA por prueba; se apaga con `PRUEBAS_ENTREVISTADOR=0`.
- No se tocaron los jueces ni los informes. Hay que subir **app.py y public/index.html** juntos.

### v46 — Periodista con más personalidad y sin confundir los roles
- **Error corregido:** al preguntarle «¿cuántos años llevas como periodista?» (transcrito como «llevé»), respondía «Usted lleva varios años como periodista», como si el periodista fuera el vocero. Ahora:
  - Cada mensaje del vocero le llega marcado como **«[El vocero dice]: …»** y el *prompt* deja muy claro quién es quién.
  - Se le avisa que lo del vocero viene de una **transcripción automática con posibles errores**, y que interprete lo más probable («llevé» → «llevas»).
  - Si le preguntan por él, contesta **en primera persona** con un dato de su personaje («Llevo dieciocho años en esto») y vuelve a lo suyo.
- **Más personalidad:** 44 años, 18 cubriendo medio ambiente y crisis de empresas; directo, frases cortas que pegan, ironía leve permitida, habla por los afectados. **Prohibidas** las frases serviles («entiendo su molestia», «tomo nota de su comentario», «gracias por su respuesta»…).
- **Ejemplos de reacción** en el *prompt* para «no me interesa», «no sé», «¿quién eres?», «cállate o te denuncio», frases ofensivas, silencios y evasivas. Lleva la cuenta de lo que el vocero esquiva («Es la tercera vez que no me responde…»).
- **Modelo más inteligente para el entrevistador:** en Groq usa primero `openai/gpt-oss-120b` con razonamiento bajo (rápido) y `llama-3.3-70b-versatile` de respaldo. Se cambia con `MODELOS_GROQ_ENTREVISTADOR`. Los jueces no cambian.
- Se quitan acotaciones entre paréntesis («(pausa)», «(con firmeza)»), que la voz leería.
- Hay que subir **app.py y public/index.html** juntos.

### v45 — El entrevistador se comporta como un periodista real
- **Problema:** el entrevistador solo "disparaba preguntas": no respondía si le preguntabas algo («¿quién eres?»), no notaba los silencios y dejaba pasar frases ofensivas o amenazas. En parte era culpa de la v43, que le prohibía presentarse.
- **Ahora tiene identidad y contexto:** es *Andrés Molina, de Panorama Regional* (nombre y medio **ficticios**; se cambian con `PERIODISTA_NOMBRE` y `PERIODISTA_MEDIO` en Vercel), en una entrevista **en vivo**, dos días después del derrame, con vecinos sin agua y molestos.
- **Reacciona antes de preguntar**, en una frase corta, a lo que acabas de decir:
  - Si le preguntas algo a él, contesta breve y vuelve a su pregunta.
  - Si te quedas callado o dices algo sin sentido, lo hace notar y repite o reformula la pregunta pendiente.
  - Si evades, insiste en lo concreto (cifras, plazos, responsables); si das un dato nuevo, profundiza.
  - Si dices algo ofensivo o que desprecia a los afectados, lo señala como grave y pregunta si es la posición oficial de la empresa.
  - Si lo insultas o amenazas, mantiene la calma, deja claro que no se deja intimidar y vuelve a lo importante.
  - Recuerda lo que no le respondiste y vuelve sobre eso; varía cómo pregunta (no empieza todo con «¿Puede…?»).
- **Sabe en qué pregunta va:** en la 1ª se presenta en una frase y en la 5ª hace la pregunta final más importante. Las respuestas vacías le llegan como «(silencio)».
- Formato pensado para ser leído en voz alta: de 1 a 3 frases cortas, una sola pregunta al final, sin acotaciones ni etiquetas.
- Solo cambia el comportamiento del entrevistador (el *prompt*); la voz, los jueces y la evaluación no cambian.
- Hay que subir **app.py y public/index.html** juntos.

### v44 — El entrevistador dice SOLO la pregunta
- **Arreglo:** en la v43 la indicación de tono («Di con tono serio y firme…») iba dentro del texto que Gemini lee, y el modelo la leía en voz alta antes de la pregunta. Ahora **el texto que Gemini lee es exactamente la pregunta**, palabra por palabra, y nada más.
- El tono (serio → incisivo → presionando) va como **instrucción de sistema**, aparte del texto: no se lee.
- **Doble protección:**
  - Si un modelo no acepta la instrucción de sistema (error 400), se usa solo el texto y se recuerda para ese modelo.
  - Si el audio dura bastante más de lo que toma leer la pregunta (señal de que leyó algo más), se descarta, se pide de nuevo solo con el texto y desde ahí ese modelo recibe solo la pregunta.
- `VOZ_GEMINI_SIN_DIRECCION=1` en Vercel apaga el tono por completo (Gemini recibe solo la pregunta, sin instrucción de sistema).
- Hay que subir **app.py y public/index.html** juntos.

### v43 — Groq para las preguntas, voz más rápida y sin monólogos
- **Groq genera las preguntas** (es el más rápido, ≈0,5 s) y **Gemini queda para la voz**. El entrevistador tiene su propio orden: variable `PROVEEDORES_ENTREVISTADOR` (por defecto `groq,gemini,anthropic,openrouter,nvidia`). Los jueces siguen con `PROVEEDORES_IA`. Si Groq falla, el entrevistador pasa solo a Gemini.
- **Voz más rápida:**
  - Se usa primero **Gemini 3.8 Flash-Lite TTS** (el más rápido); Flash y 3.1 quedan de respaldo.
  - La indicación de tono ahora es **una frase corta** («Di con tono serio y firme, a ritmo ágil: …»): menos texto para procesar y **ritmo de lectura más ágil**.
  - Se **recorta el silencio** del principio y del final del audio: la voz empieza a sonar apenas se reproduce.
  - El aviso encima de la pregunta muestra cuántos segundos tardó Gemini en generar la voz.
- **Sin monólogos antes de la pregunta:**
  - La indicación larga de la v42 hacía que Gemini improvisara antes de leer. Ahora es corta y con el formato que recomienda Google.
  - **Protección por duración:** si el audio dura mucho más de lo que toma leer la pregunta (≈15 letras por segundo), se descarta y se pide de nuevo **solo con el texto** de la pregunta.
  - El entrevistador recibe la orden de responder **solo lo que le dice al vocero** (sin presentarse ni explicar), y el servidor limpia etiquetas como «Periodista:», comillas, negritas o razonamientos (`<think>`).
- El 🩺 Diagnóstico muestra el orden de los **jueces** y del **entrevistador** por separado.
- No se probó *streaming* de voz: la documentación de Google dice que su TTS no lo soporta y hay reportes de audio cortado.
- Hay que subir **app.py y public/index.html** juntos (requirements.txt no cambia).

### v42 — Voz REALISTA del entrevistador con Gemini (gratis) + arreglo del error 410
- **Voz realista con emociones:** el servidor convierte cada pregunta en audio con **Gemini TTS** (plan gratuito de la API de Gemini, la misma `GEMINI_API_KEY`). Un "director de voz" le indica el tono: **serio y firme** en la 1ª pregunta, **incisivo y escéptico** en la 2ª y 3ª, y **presionando** desde la 4ª o cuando la pregunta insiste ("insisto", "no ha respondido", "sí o no"...). Español latino neutro.
- Endpoint nuevo: `POST /api/voz_entrevistador` (`{texto, numero}`) → audio WAV. Solo recibe el **texto de la pregunta** que generó la IA; nada del vocero.
- **Cascada de modelos de voz:** `MODELOS_GEMINI_TTS` (por defecto `gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview`). Si uno no existe (404/410) se salta 1 hora; si se acaba su cuota (429) se salta 15 minutos y se usa el siguiente. Cada modelo tiene su propia cuota gratuita (≈100 al día), así que con 3 modelos alcanza para ≈60 entrevistas diarias.
- **Respaldo automático:** si Gemini no responde en ~13 s, no hay clave, se acabó la cuota o falla, la página usa la **voz del navegador** (la de la v41) sin que la entrevista se detenga. El aviso encima de la pregunta dice qué voz se está usando y por qué.
- **Voz:** `VOZ_GEMINI_TTS` (por defecto `Orus`, masculina y firme). Otras voces de Gemini que suenan serias: `Charon`, `Alnilam`, `Kore` (femenina). Si el nombre no existe para un modelo, se usa la voz por defecto de ese modelo. `VOZ_GEMINI_SIN_DIRECCION=1` manda solo el texto, sin indicaciones de tono (por si algún modelo llegara a leer las indicaciones en voz alta).
- **🔁 Repetir pregunta** reutiliza el audio ya generado (no gasta cuota). **⏭ Saltar lectura** también cancela el audio mientras se está preparando ("🎙 preparando la voz…").
- Se mantienen las reglas de la v41: mientras habla el entrevistador no se mide tu voz y Responder/Hablar quedan bloqueados.
- 🩺 **Diagnóstico:** fila nueva **«Voz realista (Gemini)»**, que genera una frase de prueba (gasta 1 de la cuota diaria) y dice qué modelo y voz respondieron. También `tu-link/api/salud?probar_voz=1`. Para ver los modelos de voz que acepta tu clave: `tu-link/api/salud?modelos=tts`.
- **Arreglo del error 410:** cuando un proveedor retira un modelo (como pasó con `z-ai/glm-5.3` de NVIDIA el 28-oct-2026), ahora se prueba el siguiente modelo de la lista igual que con un 404, y el mensaje lo explica.
- Privacidad: en el plan gratuito Google puede usar lo que se le envía para mejorar sus productos. Aquí solo se envía el texto de la pregunta (generado por la IA).
- `requirements.txt` **no cambia** (la llamada a Gemini usa la biblioteca estándar de Python). Hay que subir **app.py y public/index.html** juntos.

### v41 — El entrevistador lee las preguntas en voz alta
- **Voz del entrevistador** con la síntesis de voz **del navegador** (gratis, sin clave y sin pasar por el servidor). Elige sola la mejor voz en español: primero de Chile, luego Latinoamérica y luego España, y prefiere las voces «Natural» de Edge o las de Google. Las preguntas siguen apareciendo escritas.
- **Para que no ensucie la evaluación:**
  - Mientras el entrevistador habla, **la medición de tu voz está apagada**. Empieza cuando termina de leer, así no se mide su voz ni se cuenta como «demora antes de hablar».
  - Mientras habla, **🎤 Hablar y Responder quedan bloqueados**. Si no quieres esperar, está **⏭ Saltar lectura**.
  - Si respondes con el micrófono todavía abierto, se apaga antes de la siguiente pregunta y lo que llegue tarde se descarta (antes podía transcribir al entrevistador dentro de tu próxima respuesta).
  - Si el navegador corta la escucha solo, el botón vuelve a «🎤 Hablar» (antes se quedaba en «Detener»).
- **🔊 Voz del entrevistador: activada / desactivada** (encima de la pregunta, se recuerda en el navegador). **🔁 Repetir pregunta** la vuelve a leer aunque esté desactivada, pausando la medición de tu voz mientras habla.
- **Si el navegador no puede hablar** (sin voces, bloqueado o falla), la pregunta queda solo escrita y la entrevista sigue igual: hay protecciones de tiempo para que nunca se quede pegada (si no empieza a hablar en 3,5 s, se suelta).
- El 🩺 Diagnóstico tiene una fila nueva, **«Voz del entrevistador»**, que dice qué voz se usa.
- **Recomendación:** usar **audífonos**. Si el entrevistador sale por los parlantes y tú hablas encima, el micrófono puede captarlo. En **Edge** las voces en español suenan bastante más naturales que en Chrome.
- Privacidad: las voces «en línea» (Natural de Edge, las de Google en Chrome) envían a Microsoft o Google **solo el texto de la pregunta** que genera la IA, nunca tu voz ni tus respuestas.
- El backend no cambió (solo `VERSION`). Hay que subir **app.py y public/index.html** juntos.

### v40 — Toda la página con el esquema de la maqueta
- **Estructura de la maqueta**: barra superior (título, modo claro/oscuro, paleta de color, chip del usuario con «Cerrar sesión») y **menú lateral** con las pantallas del Usuario final. Sigue siendo un solo `public/index.html`.
- **Menú**: 1 Inicio / mis prácticas · 2 Elegir escenario · 3 Consentimiento + check técnico · 4 Sesión de práctica · 5 Informe tipo coach · 6 Mi progreso · y la pestaña **«Diagnóstico, entrevistas y más»** con todo lo que se construyó en la beta (Diagnóstico, Mis entrevistas, Acerca de esta beta).
- **Inicio**: sesiones completadas, última nota global y área a mejorar (salen de las entrevistas guardadas), escenario con botón «Practicar» y 3 microlecciones (texto de ejemplo, falta validación de un experto).
- **Elegir escenario**: hay 1 escenario real (derrame químico); «Retiro de producto» e «Incidente de seguridad» salen como «Próximamente».
- **Mi progreso**: gráfico de tendencia, tabla de avance por área (cambio vs la entrevista anterior), recomendaciones (las «áreas a trabajar» de la última entrevista) y calendario para marcar la próxima sesión (recordatorio guardado solo en el navegador).
- **Modo claro/oscuro y 4 paletas** (se recuerdan en el navegador). El login también las sigue.
- La entrevista, cámara, voz, calibración, informes, diagnóstico y el backend **no cambiaron**; solo se reubicaron dentro del nuevo esquema.
- **No implementado a propósito**: selector de idioma ES/EN/PT, logo del cliente y las pantallas de Admin del cliente y Configurador maestro.
- Hay que subir **app.py y public/index.html** juntos (y, si la tienen, `VoxReady_logo.png` en `public/`).

### v39 — Inicio de sesión con el diseño de la maqueta (paso 1 de la migración)
- Se partió de nuevo desde la v38 (un solo `public/index.html`; se descartó la idea de meter una página dentro de otra).
- **Solo cambió el aspecto de la pantalla de inicio de sesión / crear cuenta**, para que se parezca a la de la maqueta: fondo gris claro, tarjeta blanca centrada, logo arriba, título "Inicia sesión en VoxReady", campos con etiquetas en mayúscula pequeña, botón naranja, tipografía Poppins. Funciona igual que antes (cuentas guardadas en el navegador).
- El logo busca `VoxReady_logo.png` junto a `index.html` (en `public/`); si no existe, muestra el logo en texto.
- No se tocó nada más (entrevista, cámara, voz, informes, backend). Siguiente pantalla de la maqueta: por definir.
- Hay que subir **app.py y public/index.html** juntos.

### v38 — Evaluación más estricta y precisa (muletillas, respeto) + links de curso
- **Detección de muletillas** ("eh", "o sea", "este", "tipo", "em", "po", etc.): se cuentan por código en cada respuesta y se le pasa el porcentaje real al juez de contenido. Si es alto (8% o más), tiene que bajar "Claridad del mensaje" y decirlo en el comentario.
- **Detección de lenguaje grosero o irrespetuoso** hacia el periodista (insultos, groserías, descalificaciones): si aparece en cualquier respuesta, la nota de **"Responsabilidad y tono" se limita a 1/5 por código**, sin importar lo que decida la IA (igual que ya pasaba con respuestas vacías o sin sentido). Las otras 3 áreas se evalúan normal.
- Rúbrica de contenido más estricta: respuestas vacías/sin sentido en su mayoría ahora exigen 0 o 1 en las 4 áreas (antes dejaba margen a 1-2), y se subió el mínimo de palabras para que una respuesta no se considere "de relleno" (8 → 10 palabras).
- **Botón "📚 Curso de [área]"** en cada ítem de "Áreas a trabajar" (por ejemplo, si falta empatía, aparece "Curso de Empatía"). **Todavía no lleva a ningún curso real** — son rutas placeholder (`/cursos/empatia`, `/cursos/expresion`, `/cursos/tono-de-voz`, `/cursos/coherencia`) que hoy no existen; se conectan después. Se definen en `app.py`, diccionario `CURSOS_POR_AREA`.
- Hay que subir **app.py y public/index.html** juntos.

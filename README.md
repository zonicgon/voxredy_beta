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

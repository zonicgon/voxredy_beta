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

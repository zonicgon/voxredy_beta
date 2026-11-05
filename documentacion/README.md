# VoxReady

Plataforma web SaaS de entrenamiento de voceros de crisis mediante inteligencia artificial. Permite a voceros de organizaciones practicar entrevistas de crisis simuladas y recibir una evaluación multimodal automatizada de su desempeño en **expresión, tono de voz, coherencia y empatía**.

**Integrantes:** Bernardo Araya y Bryan Retamales
**Profesor guía:** José Luis Pino Cofre
**Año:** 2026
**Mandante:** Alloxentric - Visum

---

## Índice

- [El problema](#el-problema)
- [Qué hace VoxReady](#qué-hace-voxready)
- [Arquitectura](#arquitectura)
- [Stack tecnológico](#stack-tecnológico)
- [Modelo de roles](#modelo-de-roles)
- [Requerimientos](#requerimientos)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Instalación y puesta en marcha](#instalación-y-puesta-en-marcha)
- [Variables de entorno](#variables-de-entorno)
- [Metodología de trabajo](#metodología-de-trabajo)
- [Estado del proyecto](#estado-del-proyecto)
- [Documentación relacionada](#documentación-relacionada)

---

## El problema

En momentos de alta exposición reputacional (retiros de productos, fallas de servicio, incidentes de seguridad), las organizaciones suelen fallar no por la crisis en sí, sino por una comunicación pública deficiente de sus voceros: mensajes inconsistentes, falta de calma, o un lenguaje corporal y tono de voz que contradicen lo que se comunica.

Hoy, la preparación de voceros depende casi por completo de coaching presencial: costoso, esporádico, y sin una métrica objetiva ni consistente entre sesiones.

## Qué hace VoxReady

VoxReady reemplaza esa dependencia con una plataforma de autoaprendizaje disponible en cualquier momento:

1. El vocero **selecciona un escenario de crisis** configurado por el administrador de su organización.
2. Otorga **consentimiento biométrico explícito** (normativa UK GDPR) y pasa una **comprobación técnica** de cámara/micrófono.
3. Vive una **entrevista simulada en tiempo real**: un entrevistador impulsado por IA genera preguntas y repreguntas adaptativas, con transcripción de voz a texto en vivo.
4. El sistema ejecuta un **pipeline de análisis multimodal** sobre la sesión: contenido (LLM como juez contra un patrón maestro), voz (prosodia, ritmo, pausas, muletillas) e imagen (expresión facial y postura).
5. Las tres señales se combinan en un **informe tipo coach**: puntaje global, desglose por las 4 áreas, recomendaciones vinculadas a microlecciones y una agenda de práctica.
6. Los casos de baja confianza se derivan a **revisión humana**, cuyas correcciones quedan registradas para mejorar el sistema.

## Arquitectura

```
┌─────────────┐    ┌──────────────────┐    ┌───────────────────────┐    ┌──────────────┐
│  Pre-sesión │ →  │  Entrevista en   │ →  │  Pipeline de análisis │ →  │  Informe     │
│  (consenti- │    │  tiempo real     │    │  multimodal           │    │  coach       │
│  miento +   │    │  (LLM + STT en   │    │  (contenido, voz,     │    │  (fusión +   │
│  chequeo    │    │  streaming)      │    │  imagen)              │    │  progreso)   │
│  técnico)   │    │                  │    │                       │    │              │
└─────────────┘    └──────────────────┘    └───────────────────────┘    └──────────────┘
```

Decisiones de arquitectura clave (ver tesis, sección *Fundamentación y Factibilidad*):

- **Backend serverless:** API REST en FastAPI, empaquetada en Docker, desplegada en Vercel.
- **Resiliencia de proveedores de IA:** conmutación automática (*failover*) en cadena — `Gemini → Claude → Groq → OpenRouter → NVIDIA` — para que el sistema no quede inoperante si un proveedor falla o descontinúa un modelo.
- **Procesamiento en el cliente (*browser-side*):** el análisis de voz (Web Audio API) y de video (MediaPipe Face Landmarker, 52 *blendshapes*) se ejecuta en el navegador, no en el servidor — así no se transmiten flujos pesados de audio/video y se reduce la latencia.
- **Privacidad por diseño:** no se almacenan archivos de video/audio en servidores; las métricas derivadas se conservan con un plazo fijo de 30 días y purga automática.
- **Multi-tenant:** los datos de cada organización están aislados mediante control de acceso basado en roles.

## Stack tecnológico

| Capa | Tecnología |
|---|---|
| Backend | FastAPI (Python), empaquetado en Docker |
| Despliegue | Vercel (serverless) |
| Modelos de lenguaje | Gemini, Claude, Groq, OpenRouter, NVIDIA NIM (con *failover* automático) |
| Voz a texto | Web Speech API / Speech Recognition del navegador |
| Análisis de voz | Web Audio API (cliente) |
| Análisis de imagen | MediaPipe Face Landmarker (cliente) |
| Persistencia de sesión | `localStorage` con expiración (TTL 30 días) |
| Control de versiones | Git / GitHub (despliegue continuo a Vercel) |

> Durante el desarrollo se construyó y validó un prototipo con un backend FastAPI más simple, usando únicamente la API de NVIDIA (`integrate.api.nvidia.com`) y almacenamiento en archivos locales (`transcripciones/`, `audios/`, `informes/`). Ese prototipo demostró el flujo completo (entrevista → transcripción → evaluación de contenido → evaluación de voz) antes de incorporar el resto del stack descrito arriba.

## Modelo de roles

| Rol | Qué hace |
|---|---|
| **Vocero** | Practica las simulaciones y consulta su informe y progreso. |
| **Administrador de organización** | Configura escenarios y mensajes clave, y revisa el progreso de los voceros de su empresa. |
| **Configurador maestro** | Define el patrón maestro de vocería y las rúbricas de evaluación. |
| **Evaluador humano** | Revisa y calibra los casos de baja confianza derivados por el sistema. |

## Requerimientos

El detalle completo de requerimientos funcionales (RF-01 a RF-12) y no funcionales (RNF-01 a RNF-13 — seguridad, privacidad UK GDPR, aislamiento multi-tenant, IA responsable, fiabilidad, rendimiento, usabilidad y mantenimiento) está documentado en la tesis del proyecto, sección *Requerimientos*.

## Estructura del proyecto

```
voxready/
├── backend/                  # API FastAPI, orquestación multi-IA, pipelines de análisis
├── frontend/                 # Captura (consentimiento, cámara/mic), entrevista, informe
├── docs/                     # Documentación Scrum, tesis, diagramas
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

> [Ajustar esta estructura para que coincida exactamente con las carpetas reales del repositorio.]

## Instalación y puesta en marcha

### Requisitos previos

- Python 3.9 o superior
- Node.js (si el frontend se sirve como proyecto Vercel)
- Docker (para levantar el backend containerizado)
- Google Chrome o Microsoft Edge (requeridos por las APIs de voz y cámara del navegador)
- Al menos una API key de un proveedor de IA de la cadena de *failover* (Gemini, Claude, Groq, OpenRouter o NVIDIA)

### Pasos

```bash
# 1. Clonar el repositorio
git clone <url-del-repositorio>
cd voxready

# 2. Instalar dependencias del backend
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# completar .env con las API keys correspondientes

# 4. Levantar el backend (local, sin Docker)
uvicorn backend.main:app --reload --port 8001

# — o, empaquetado en Docker —
docker compose up --build

# 5. Abrir el frontend
# servir la carpeta frontend/ (por ejemplo, con un servidor estático)
python -m http.server 8000
```

> [Confirmar y ajustar comandos exactos según la organización final de carpetas y el punto de entrada real del backend.]

## Variables de entorno

| Variable | Descripción |
|---|---|
| `GEMINI_API_KEY` | API key de Gemini (Google) — primer proveedor en la cadena de *failover*. |
| `ANTHROPIC_API_KEY` | API key de Claude (Anthropic) — segundo proveedor. |
| `GROQ_API_KEY` | API key de Groq — tercer proveedor. |
| `OPENROUTER_API_KEY` | API key de OpenRouter — cuarto proveedor. |
| `NVIDIA_API_KEY` | API key de NVIDIA NIM (`build.nvidia.com`) — último proveedor de respaldo. |

Nunca se suben al repositorio: viven solo en `.env`, que está excluido por `.gitignore`.

## Metodología de trabajo

El proyecto se desarrolla con **Scrum**, adaptado a un equipo de 2 integrantes (equipo Core), en sprints semanales de 18 semanas. El detalle de roles, ceremonias, Product Backlog, Sprint Backlogs y definición de terminado está en la documentación Scrum del proyecto (ver [Documentación relacionada](#documentación-relacionada)).

## Estado del proyecto

| Hito | Período | Estado |
|---|---|---|
| Hito 1 — MVP con base de datos operativa / persistencia base | 28 sep – 3 oct | Cumplido vía persistencia en cliente |
| Hito 2 — Proyecto en Docker y pipeline multimodal | 26 oct – 31 oct | Cumplido 100% |
| Hito 3 — Entrega final, informe coach y documentación | 16 nov – 4 dic | En etapa de cierre |

## Documentación relacionada

- Tesis / documento de producto completo (contexto, requerimientos, metodología)
- Documentación Scrum — Fundacionales (Visión del producto, Roles, Definición de Terminado)
- Documentación Scrum — Product Backlog, historias de usuario y Plan de Sprints
- Manual técnico *(pendiente)*
- Diagramas de arquitectura y modelo de datos *(pendiente)*

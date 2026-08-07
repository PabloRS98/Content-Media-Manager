# Auditoría técnica — Content-Media-Manager

Revisión completa del código en `claude/project-review-audit-omj87n`, sobre el commit
`532ccc3`. Cubre seguridad, corrección funcional, robustez, rendimiento, y prácticas de
build/CI.

Cada hallazgo marcado como **verificado** se reprodujo ejecutando la aplicación real con
`TestClient` sobre una base de datos temporal; el resto son observaciones de lectura de
código, señaladas como tales.

> **Estado: todos los hallazgos (22/22) están corregidos**, mergeados antes de la
> versión [1.0.0](../CHANGELOG.md#100--2026-08-03). Este documento se conserva tal
> cual quedó tras la auditoría original -- como registro de qué se encontró y por
> qué -- y no se ha reescrito hallazgo por hallazgo. Para el detalle de qué PR
> arregló cada cosa, ver el [CHANGELOG](../CHANGELOG.md) y el historial de pull
> requests cerradas del repositorio.

> **Hay una segunda auditoría.** En agosto de 2026 se revisó de nuevo el proyecto
> entero, junto a las otras dos aplicaciones self-hosted, y salieron 44 hallazgos
> nuevos con el prefijo `MC-*`. Esa segunda ronda **no repite ninguno de los de
> aquí**: se comprobó que los patrones corregidos siguen corregidos (el saneado
> de esquemas de URL, el escapado de `LIKE`, el límite de subida, el `tojson` de
> Jinja y el guard anti-SSRF de los feeds) y todos aguantan. El registro de qué
> cerró cada uno está en el [CHANGELOG](../CHANGELOG.md), por identificador.

---

## Resumen ejecutivo

El proyecto está bien construido para lo que pretende ser: una app self-hosted,
mono-usuario, sin build de frontend y sin dependencias de pago. La separación
`routers` / `services` / `models` es limpia, el manejo de fallos de red en los servicios
externos es en general defensivo (best-effort con fallback), y la migración ligera de
columnas (`ensure_columns`) resuelve el problema de esquema sin arrastrar Alembic.

Dicho eso, la auditoría encontró **22 hallazgos**, de los cuales 3 son de severidad alta
y merecen arreglo antes de exponer la app fuera de la LAN (algo que el propio README
sugiere hacer vía Tailscale). El punto más débil del proyecto no es ninguno de los bugs
individuales sino la **ausencia total de tests**: el CI solo compila y hace un smoke test
de `/salud`, así que ninguno de los errores de lógica listados abajo habría sido detectado.

| Severidad | Nº |
|---|---|
| Alta | 3 |
| Media | 9 |
| Baja | 10 |

---

## Severidad alta

### A1. XSS almacenado en `/estadisticas` — el filtro `tojson` está sobreescrito ✅ verificado

`app/templating.py:7`

```python
templates.env.filters["tojson"] = lambda value: json.dumps(value, default=str)
```

Jinja2 trae un filtro `tojson` propio (`htmlsafe_json_dumps`) que escapa `<`, `>`, `&` y
`'` como secuencias `\uXXXX` **precisamente** para poder incrustar datos dentro de un
bloque `<script>` sin que se pueda cerrar la etiqueta. Esta línea lo reemplaza por un
`json.dumps` crudo, que no escapa nada.

`app/templates/stats.html:129` incrusta datos controlados por el usuario dentro de
`<script>`:

```jinja
labels: {{ top_generos | map(attribute=0) | list | tojson | safe }},
```

`top_generos` sale de `MediaItem.genres`, un campo de texto libre editable desde
`/item/{id}/actualizar` y también rellenado desde los CSV de importación.

**Reproducción.** Guardando el género `</script><img src=x onerror=alert(1)>` en cualquier
ítem, `GET /estadisticas` devuelve 200 con el payload literal en el HTML:

```
labels: ["</script><img src=x onerror=alert(1)>"],
```

**Impacto.** En una app mono-usuario esto empieza como self-XSS, pero deja de serlo en dos
casos realistas: (a) importar un CSV de un tercero (una lista de Goodreads compartida), y
(b) combinado con **A2/M1** — una página maliciosa puede hacer `POST /agregar` sin token
CSRF y plantar el género envenenado. A partir de ahí el atacante ejecuta JS con la sesión
del usuario y acceso a toda la app.

**Arreglo.** Eliminar el override. Jinja ya trae `tojson` y hace exactamente lo que se
necesita; si hace falta serializar `date`/`datetime`, registrar un filtro con otro nombre
o configurar `policies["json.dumps_kwargs"]`, no pisar el built-in. Una vez eliminado, los
`| safe` de `stats.html` siguen siendo correctos porque `tojson` ya devuelve markup seguro.

---

### A2. Open redirect en `/catalogo/completar-portadas` vía cabecera `Referer` ✅ verificado

`app/routers/catalog.py:256-257`

```python
referer = request.headers.get("referer") or "/catalogo"
return redirect_flash(referer, msg)
```

La cabecera `Referer` la controla íntegramente el cliente y se usa como destino de un 303
sin ninguna validación.

**Reproducción.** `POST /catalogo/completar-portadas` con `Referer: https://evil.example.com/phish`
responde `303` con `Location: https://evil.example.com/phish`.

**Impacto.** Redirección abierta clásica: sirve para phishing (el enlace sale del dominio
de confianza del usuario) y encadena bien con A1. Además, si algún día se añaden tokens en
querystring, se filtran al destino externo.

**Arreglo.** Aceptar solo rutas internas:

```python
referer = request.headers.get("referer") or ""
path = urlparse(referer).path or "/catalogo"
destino = path if path.startswith("/") and not path.startswith("//") else "/catalogo"
```

---

### A3. Las API keys se escriben en los logs cuando una API externa falla ✅ verificado

`app/services/tmdb.py:34,67,132,175,209,239`, `app/services/rawg.py:39`,
`app/services/googlebooks.py:102`

Todos estos servicios pasan la clave como query param (`params={"api_key": ...}`) y
capturan los fallos con `logger.exception(...)`. El mensaje que genera
`httpx.Response.raise_for_status()` incluye la URL completa:

```
Client error '401 Unauthorized' for url
'https://api.themoviedb.org/3/search/movie?api_key=SUPER_SECRET_KEY_123&query=dune'
```

**Impacto.** Basta un 401/429/500 de TMDB, RAWG o Google Books para que la clave quede
escrita en el log de la aplicación. Con `docker-compose.yml` esos logs van al driver
`json-file`, es decir, persisten en disco y aparecerían en cualquier `docker logs` que el
usuario pegue en un issue de GitHub para pedir ayuda. TMDB y RAWG son claves gratuitas,
pero siguen estando ligadas a la cuenta del usuario y son revocables/abusables.

**Arreglo.** Dos opciones, no excluyentes:
1. Mandar la clave en cabecera en lugar de en la query donde la API lo permita (TMDB
   acepta `Authorization: Bearer` con el token v4).
2. No volcar la excepción cruda: capturar `httpx.HTTPStatusError` y loguear solo
   `e.response.status_code` y el endpoint, sin `params`. Como mínimo, sustituir
   `logger.exception` por `logger.warning("TMDB %s devolvió %s", endpoint, status)`.

---

## Severidad media

### M1. No hay protección CSRF en ninguna operación de escritura

Todos los `POST` (`/agregar`, `/item/{id}/actualizar`, `/item/{id}/eliminar`,
`/listas`, `/importar`, `/catalogo/completar-portadas`…) aceptan formularios sin token
anti-CSRF ni comprobación de `Origin`/`Sec-Fetch-Site`.

Con `ENABLE_AUTH=true` la situación empeora en vez de mejorar: HTTP Basic hace que el
navegador reenvíe las credenciales automáticamente en peticiones cross-site, así que
cualquier página que el usuario visite puede borrar ítems o crear entradas en su catálogo.
Sin auth, cualquiera con acceso de red a la app puede hacer lo mismo directamente.

**Arreglo.** Para una app de este tamaño no hace falta librería: un middleware que rechace
peticiones no seguras cuyo `Sec-Fetch-Site` no sea `same-origin`, con fallback a comparar
`Origin` contra el `Host`, cubre el caso a coste casi cero.

### M2. El importador de IMDb crea filas duplicadas dentro del mismo CSV ✅ verificado

`app/routers/imdb_import.py:127-131` + `app/database.py:23`

El dedupe consulta la BD (`db.query(MediaItem).filter(MediaItem.external_id == ...)`), pero
`SessionLocal` está configurado con `autoflush=False`, así que las filas añadidas con
`db.add()` en iteraciones anteriores **todavía no están en la BD** cuando se hace la
consulta. El `db.commit()` es único y va al final del bucle.

**Reproducción.** Un CSV con la misma línea `tt0000001,Dup Movie,movie,2001` repetida dos
veces crea **2 ítems** (esperado: 1, contando 1 duplicado).

**Impacto.** Los exports de IMDb con la misma película en "Ratings" y en "Watchlist", o un
usuario que reimporta un fichero ligeramente editado, acaban con catálogo duplicado. No
hay `UniqueConstraint` en `MediaItem.external_id` (`app/models.py:91`) que actúe de red de
seguridad.

**Arreglo.** Mantener un `set()` de `external_id` ya vistos en el bucle — igual que ya hace
correctamente `app/services/imports.py:122` con `existing.add(key)` — y añadir un índice
único sobre `external_id`.

### M3. `enrich_missing_covers` reporta mal cuántos ítems quedan pendientes ✅ verificado

`app/services/enrich.py:132`

```python
"restantes": max(0, total_missing - len(batch)),
```

Resta el tamaño del lote **procesado**, no el número de portadas **encontradas**. Los ítems
que se procesaron sin éxito siguen sin portada pero desaparecen de la cuenta.

**Reproducción.** Con 8 ítems sin portada y ninguna coincidencia encontrada:
`{'procesados': 8, 'encontrados': 0, 'restantes': 0}` — pero siguen faltando 8.

**Impacto.** La UI miente. `app/routers/catalog.py:252-254` usa ese valor para decidir entre
"Aún quedan N ítems sin portada" y "¡Todos los elementos de tu catálogo tienen portada!",
así que el usuario ve el mensaje de éxito con el catálogo a medias y deja de pulsar el
botón.

**Arreglo.** `"restantes": max(0, total_missing - found)`.

### M4. Un fallo de red en Google Books devuelve un 500 al usuario ✅ verificado

`app/services/googlebooks.py:32-53`

El bucle de reintentos solo captura `httpx.HTTPStatusError`, y para códigos que no sean
429/503 hace `raise e` explícito. Los errores de transporte (`ConnectError`,
`ConnectTimeout`, `ReadTimeout`) ni siquiera entran en el `except`. `/buscar` llama a
`search_books` sin protección (`app/routers/catalog.py:266`).

**Reproducción.** Con la salida de red caída, `GET /buscar?tipo=libro&q=dune` → **HTTP 500**.
La misma caída en TMDB → **HTTP 200** con lista vacía, porque `tmdb.py` sí envuelve todo en
un `try/except Exception`.

**Impacto.** Inconsistencia de comportamiento entre fuentes y una página de error de FastAPI
en vez del mensaje "Sin resultados" que la plantilla ya tiene preparado. Además rompe la
cascada de fallback: `search_external` nunca llega a probar Open Library porque la excepción
sale antes.

**Arreglo.** Envolver el cuerpo entero de `search_books` en `try/except Exception` y
devolver `[]`, alineándolo con `tmdb.py`, `rawg.py` y `openlibrary.py`.

### M5. Añadir un ítem ya "completado" no registra `completed_at` ✅ verificado

`app/routers/catalog.py:320-336`

`add_item` construye el `MediaItem` sin tocar `completed_at`. `update_item` sí lo hace
(`app/routers/catalog.py:434-435`), y el importador de IMDb también
(`app/routers/imdb_import.py:164`) — es solo el alta directa la que se olvida.

**Reproducción.** `POST /agregar` con `status=completado` deja `completed_at = None`.

**Impacto.** Toda la página de estadísticas se apoya en `completed_at`: "completados este
año", el gráfico por meses, "mejores del año", y el bloque de "actividad reciente" del
inicio (`app/routers/home.py:83-89`). Un ítem añadido ya como completado es invisible en
todos ellos.

**Arreglo.** Aplicar en `add_item` la misma regla que en `update_item`:
`completed_at=date.today() if status == MediaStatus.COMPLETADO else None`.

### M6. N+1 de consultas en `/estadisticas` ✅ verificado

`app/routers/catalog.py:542-543`

```python
for ep in db.query(Episode).join(MediaItem).filter(Episode.watched.is_(True)):
    tiempo_min += ep.runtime_minutes or ep.item.runtime_minutes or 45
```

El `join` filtra pero no carga la relación, así que cada acceso a `ep.item` dispara un
`SELECT` diferido. Los cuatro bucles anteriores (líneas 536-541) tampoco necesitan
materializar objetos: son sumas que SQLite puede hacer con `func.sum`.

**Reproducción.** Con 30 series × 10 episodios vistos, `/estadisticas` ejecuta **44
sentencias SQL**; con el join cargado serían ~14.

**Impacto.** Crece linealmente con el catálogo. Con 500 series la página pasa a ~500
consultas.

**Arreglo.** `.options(joinedload(Episode.item))` en esa consulta, y sustituir los cuatro
bucles de agregación por `func.sum(...)` en una sola query.

### M7. SSRF potencial a través de `external_id` en podcasts

`app/routers/catalog.py:309` + `app/services/itunes.py:66`

`add_item` acepta `external_id` como campo de formulario libre. Para podcasts ese campo
**es la URL del feed RSS**, y `fetch_podcast_episodes` la pide con
`httpx.get(feed_url, timeout=15, follow_redirects=True)` — sin validar el esquema, sin
lista blanca de hosts, y siguiendo redirecciones.

Un `POST /agregar` con `media_type=podcast`, `external_source=itunes` y
`external_id=http://169.254.169.254/latest/meta-data/` hace que el servidor emita esa
petición. El cuerpo no se devuelve al atacante (se parsea como XML y falla), así que es un
SSRF ciego, pero suficiente para escanear la red interna por diferencias de temporización.
Encadena con M1 (CSRF), que es lo que permite disparar el `POST` desde fuera.

**Arreglo.** Validar que `feed_url` empieza por `https://` (o `http://`) y rechazar hosts
que resuelvan a rangos privados/loopback/link-local antes de la petición.

### M8. `/importar` lee el fichero entero en memoria sin límite de tamaño

`app/routers/imdb_import.py:101`, y las mismas líneas en `import_books` (`:183`) e
`import_games` (`:193`):

```python
contenido = (await archivo.read()).decode("utf-8-sig", errors="ignore")
```

`UploadFile.read()` sin argumento carga todo el fichero, y luego `io.StringIO` mantiene una
segunda copia decodificada. No hay tope de tamaño ni comprobación de `content-type`.

**Impacto.** Subir un fichero de 1 GB consume ~2-3 GB de RSS y tumba el contenedor. En una
app mono-usuario el vector realista es un accidente (arrastrar el fichero equivocado) más
que un ataque, pero el fallo es un OOM del proceso entero, no un error controlado.

**Arreglo.** Leer por trozos con un tope (p. ej. 20 MB) y devolver un `413` al superarlo.

### M9. El enriquecimiento de portadas bloquea la petición HTTP hasta minutos

`app/services/enrich.py:105-127`

`enrich_missing_covers` procesa `BATCH_SIZE = 30` ítems de forma síncrona dentro del
request, con `time.sleep(0.7)` entre cada uno y hasta 3 llamadas HTTP encadenadas por ítem
(Google Books → Wikipedia → Open Library), cada una con `timeout=10`.

El suelo son 21 s solo de `sleep`; el techo, con las APIs lentas, supera los 2 minutos. Al
ser un endpoint `def` (no `async def`) no bloquea el event loop, pero sí ocupa un hilo del
threadpool durante todo ese rato, y cualquier proxy inverso delante cortará por timeout
antes de que termine.

**Arreglo.** Mover el lote a `BackgroundTasks` (o a un job del `BackgroundScheduler` que ya
existe) y que el fragmento HTMX haga polling del progreso.

---

## Severidad baja

### B1. El filtro de géneros no escapa los comodines de `LIKE` ✅ verificado

`app/routers/catalog.py:77` — `MediaItem.genres.like(f"%{genero}%")`. No es inyección SQL
(SQLAlchemy parametriza), pero `%` y `_` del usuario se interpretan como comodines:
`?genero=%` devuelve el catálogo entero, y `?genero=_` cualquier género de un carácter.
Ruido funcional. Arreglo: `.like(f"%{escaped}%", escape="\\")` con `%`, `_` y `\` escapados.

### B2. `_get()` se queda con la primera columna presente aunque esté vacía ✅ verificado

`app/routers/imdb_import.py:56-64`. La comprobación es `if v is not None`, así que una
columna presente con valor vacío gana a una alternativa poblada:
`_get({"Title": "", "Título": "Duna"}, "Title", "Título")` devuelve `""`. En un CSV de IMDb
bilingüe (con `Title` y `Título`), esas filas se descartan como "omitidas". La versión de
`app/services/imports.py:17-24` sí lo hace bien (`if v:`) — es una divergencia entre dos
copias de la misma función.

### B3. `BACKUP_KEEP=0` no borra ningún backup ✅ verificado

`app/services/scheduler.py:126` — `existing[:-settings.backup_keep]` con `backup_keep=0` se
evalúa como `existing[:0]`, es decir, lista vacía: no se borra nada, cuando la intención
sería lo contrario. Verificado: con `BACKUP_KEEP=0` los 3 ficheros de backup se conservan.
Arreglo: `existing[:-keep] if keep > 0 else existing[:-1]` (o rechazar 0 en la config).

### B4. Una contraseña con caracteres no ASCII bloquea el acceso permanentemente ✅ verificado

`app/auth.py:21-22`. Starlette decodifica el header Basic con `.decode("ascii")` y devuelve
401 si falla; y aunque llegara, `secrets.compare_digest` sobre `str` exige ASCII puro y
lanzaría `TypeError` → 500. Verificado: con `AUTH_PASSWORD=contraseña` y credenciales
correctas, la respuesta es **401**. Dado que el `.env.example` está en español, es una
trampa fácil de pisar. Arreglo: comparar sobre `bytes` (`.encode("utf-8")`) y documentar la
limitación.

### B5. No hay validación de rango en `rating` ni en `year`

`app/routers/catalog.py:437` acepta cualquier entero. Los `min`/`max` del HTML
(`detail.html:145`) son solo cliente. No provoca un crash porque `stats` filtra con
`if 1 <= rating <= 10` (`:531`), pero sí datos silenciosamente inconsistentes: un rating de
99 se guarda y desaparece del histograma sin avisar.

### B6. El contador "sin portada" ignora el filtro de tipo activo

`app/routers/catalog.py:134`. Cuenta sobre todo el catálogo, pero se muestra en una página
ya filtrada por `tipo`. Estando en "Libros" el usuario ve un número que incluye películas.

### B7. Rutas relativas para plantillas y estáticos

`app/main.py:68` (`directory="app/static"`) y `app/templating.py:6`
(`directory="app/templates"`). Solo funcionan si el proceso arranca desde la raíz del
repo. En Docker el `WORKDIR /app` lo salva, pero rompe cualquier otro modo de arranque.
Arreglo: `Path(__file__).parent / "static"`.

### B8. El scheduler se duplica con más de un worker de uvicorn

`app/main.py:56` arranca el `BackgroundScheduler` en el `lifespan`, que se ejecuta **una vez
por worker**. El `CMD` del Dockerfile usa un solo worker, así que hoy no ocurre; pero
cualquiera que añada `--workers 4` tendrá 4 jobs de backup pisándose el mismo fichero y
avisos de Telegram por cuadruplicado. Conviene dejarlo dicho en un comentario junto al
`CMD`, o guardarlo tras una variable de entorno tipo `ENABLE_SCHEDULER`.

### B9. El contenedor corre como root

El `Dockerfile` no tiene directiva `USER`, así que el proceso corre como root dentro del
contenedor y los ficheros de `/data` quedan a nombre de root en el volumen. Tampoco hay
`HEALTHCHECK`, pese a existir ya el endpoint `/salud` perfecto para ello. Añadir un usuario
sin privilegios y `HEALTHCHECK CMD curl -f http://localhost:8000/salud` son dos líneas.

### B10. `docker compose up` falla si no se ha creado `.env`

`docker-compose.yml:7-8` declara `env_file: .env` sin `required: false`, pero el README
presenta `cp .env.example .env` como paso **opcional**. Sin ese fichero, Compose aborta con
"env file .env not found". Arreglo: `env_file: [{path: .env, required: false}]`.

---

## Tests y CI

> Esta sección describe el estado **en el momento de la auditoría**. Desde entonces
> se añadió una suite real (190 tests a fecha de 1.0.0) que corre en CI junto con
> `ruff`; ver [CHANGELOG](../CHANGELOG.md).

**No existe ni un solo test** en el repositorio: cero ficheros `test_*.py`, sin `conftest.py`,
sin `pytest` en `requirements.txt`. El workflow `ci.yml` hace tres cosas:
`python -m compileall app`, `docker build`, y un `curl` a `/salud`.

Eso valida que el código es sintácticamente válido y que el contenedor arranca. No valida
ninguna regla de negocio. Los hallazgos A1, M2, M3, M4, M5, B2 y B3 son todos
comportamientos observables desde una petición HTTP o una llamada de función pura, y **todos
se detectan con un test de 5 líneas**. Fueron encontrados en esta auditoría exactamente así.

Recomendación mínima, por orden de rentabilidad:

1. `pytest` + `httpx` + `TestClient` con una fixture de SQLite en `tmp_path`. La app ya es
   muy testeable: `get_db` es una dependencia inyectable y toda la lógica de dominio vive en
   `services/` sin acoplarse a FastAPI.
2. Tests de las funciones puras primero — `imports._parse_date`, `imports._rating5_to_10`,
   `itunes._duration_minutes`, `imdb_import._get`, `metadata.estimated_minutes`,
   `enrich._pick_match`. Son deterministas y no necesitan red.
3. Tests de endpoint para el ciclo alta → edición → borrado, y los tres importadores de CSV
   con un fichero de ejemplo por formato.
4. En CI, añadir `ruff check` — habría marcado los imports duplicados dentro de funciones
   (`catalog.py:98-99`, `enrich.py:31`, `googlebooks.py:30`) y varias variables no usadas.
5. `pip-audit` o Dependabot: `requirements.txt` está pineado (bien), pero nada avisa cuando
   sale un CVE. `fastapi==0.115.0` y `jinja2==3.1.4` ya tienen versiones posteriores.

---

## Lo que está bien

Vale la pena dejar constancia de las decisiones acertadas, porque condicionan cómo arreglar
lo anterior:

- **Manejo defensivo de las APIs externas.** `tmdb`, `rawg`, `openlibrary`, `itunes`,
  `wikipedia_covers` y `telegram` capturan todo y devuelven un valor neutro; la app funciona
  entera sin ninguna API key configurada. Google Books (M4) es la única excepción, y por eso
  destaca.
- **`ensure_columns`** (`app/database.py:38-50`) resuelve las migraciones al nivel de
  complejidad que este proyecto necesita, y el comentario explica correctamente por qué solo
  vale para columnas nullable.
- **Escapado de plantillas.** Fuera del caso de `stats.html`, el autoescape de Jinja está
  activo y bien usado; los `| safe` de `_icons.html` son sobre un diccionario de constantes.
- **El toast de `app.js`** usa `textContent` para el mensaje (`app.js:26`) en lugar de
  `innerHTML`, evitando XSS a través de la cookie flash. Es un detalle deliberado y correcto.
- **Backups con la API `sqlite3.backup()`** (`scheduler.py:110-117`) en vez de copiar el
  fichero, que es lo correcto con WAL activado y escrituras concurrentes.
- **PRAGMA WAL + `synchronous=NORMAL`** (`database.py:16-21`) es la configuración adecuada
  para este perfil de carga.
- **Zero build de frontend**, con HTMX y la fuente Inter vendorizados: el README lo promete y
  el código lo cumple.

---

## Plan de acción sugerido

**Antes de exponer la app fuera de la LAN:** A1, A2, A3, M1.

**Siguiente iteración (bugs que afectan a datos que el usuario ve):** M2, M3, M5, M4, B2.

**Deuda técnica, por orden:** los tests (sección anterior), M6, M9, y el resto de baja
severidad según convenga.

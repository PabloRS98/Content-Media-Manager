# Tercera auditoría — Content-Media-Manager

Revisión del proyecto entero sobre el commit `3e29c9d` (octubre de 2026, tras el
rediseño). Cuatro frentes en paralelo: seguridad, backend, infraestructura y
frontend. Los identificadores llevan prefijo `R3-` para no chocar con los de las
dos auditorías anteriores ([AUDITORIA.md](AUDITORIA.md) y los `MC-*` del
[CHANGELOG](../CHANGELOG.md)).

**No se ha regresado nada de lo corregido antes.** Se comprobó expresamente: el
escapado de `LIKE`, el `tojson` de Jinja, el destino del `Referer`, la
deduplicación del importador de IMDb, `BACKUP_KEEP=0`, el escapado de Telegram,
las estadísticas agregadas en SQL y por cuenta, y el CSRF que falla cerrado.

Cada hallazgo indica cómo se comprobó:
- **[V]**: verificado ejecutando algo (test, script o navegador).
- **[L]**: deducido leyendo el código.

Los de severidad alta se volvieron a comprobar a mano antes de escribir este
documento.

> **Estado:** cerrados todos los hallazgos (R3-A1 a R3-A6, R3-M1 a R3-M30 y
> R3-B1 a R3-B34), en siete tandas descritas en el [CHANGELOG](../CHANGELOG.md).
> Los tests están en `tests/test_auditoria_3.py`, `test_seguimiento.py`,
> `test_ci.py`, `test_importadores_auditoria_3.py`, `test_bloque_6.py`,
> `test_logica_auditoria_3.py`, `test_frontend_auditoria.py`,
> `test_itunes_xml.py` y `test_migraciones_downgrade.py`. De R3-M22 queda
> cortar la primera versión con tag, que es decisión del mantenedor.

| Severidad | Nº |
|---|---|
| Alta | 6 |
| Media | 24 |
| Baja / mejora | 30+ |

---

## Lo que está bien

- **Aislamiento entre cuentas.** Se probó con dos cuentas reales. La cuenta A atacó cada ruta de ítems, episodios y listas de la B, incluidas las tres nuevas (`/estado`, `/nota`, `/progreso`). También se probaron las páginas de agregados. Todo da 303 sin tocar nada. La única fuga es el renombrado de géneros (R3-A2).
- **XSS.** Se probó con un payload en 10 campos y 4 parámetros, sobre 17 páginas, y ninguna lo refleja sin escapar. `app.js` solo usa `innerHTML` con constantes.
- **Interfaz.**
  - Sin desbordamiento horizontal: 14 páginas × 4 anchos × 2 temas.
  - Sin errores de consola.
  - Un `h1` por página.
  - Diálogos con foco atrapado y devuelto.
  - El tema oscuro pasa WCAG AA en todo.
- **Contenedor endurecido.** Solo lectura, `cap_drop ALL` y `no-new-privileges`.
- **Migraciones.** Hacen `downgrade base` y vuelven a `upgrade head` sin errores.
- **Suite.** Verde en 57 s; el test más lento tarda 0,6 s.
- **Cobertura.** 85 % de `app/`.

---

## Severidad alta

### R3-A1. Las claves de API y el token de Telegram se escriben en el log en cada petición que sale bien [V]

- **Dónde:** `app/main.py:35`, donde `logging.basicConfig(level=INFO)`.
- **Qué pasa:** el logger de `httpx` emite `HTTP Request: GET https://api.themoviedb.org/...?api_key=SECRETO`. Pasa con TMDB, RAWG, Google Books y Steam, y en `POST .../bot<TOKEN>/sendMessage` con Telegram.
- **Por qué sigue abierto:** A3 y MC-C3 solo cerraron la ruta de error. Con el driver `json-file` del compose, las claves quedan en disco y salen en cualquier `docker logs` que se pegue en un issue.
- **Arreglo:**
  - `logging.getLogger("httpx").setLevel(logging.WARNING)`, y lo mismo para `httpcore`.
  - Un test que compruebe que tras una petición correcta no aparece la clave en el log.
  - Opcional: TMDB por cabecera `Authorization: Bearer`.

### R3-A2. Renombrar o fusionar un género cambia los ítems de las demás cuentas [V]

- **Dónde:** `app/services/generos.py:139-157` y `app/routers/generos.py:35-46`.
- **Qué pasa:** `Genero` es una tabla compartida. El router solo comprueba que tengas algún ítem con ese género y luego renombra la fila para todos. Al fusionar, mueve `genero.items` de todas las cuentas.
- **Efecto:** si la cuenta A renombra «Drama» a cualquier cosa, los ítems de la cuenta B cambian también. Es una escritura cruzada entre cuentas.
- **Arreglo:** reasignar solo los ítems de la cuenta actual a un `Genero` destino, sin tocar la fila compartida. Después, `borrar_huerfanos`.

### R3-A3. Una serie al día deja de refrescarse y de avisar [V]

- **Dónde:** `app/services/scheduler.py:21` (`FOLLOWING` excluye `COMPLETADO`) y `:128-175`.
- **Qué pasa:** al marcar el último episodio emitido, `recompute_status` pone la serie en COMPLETADO. A partir de ahí el job diario no la vuelve a mirar. Cuando sale el episodio siguiente o una temporada nueva, no llega ni el episodio, ni el aviso de Telegram, ni nada en «Próximamente».
- **Arreglo:**
  - Refrescar también las episódicas COMPLETADO de TMDB. `temporadas_que_pueden_cambiar` ya limita el coste.
  - Llamar a `recompute_status` después de `load_episodes`, para que la serie vuelva a «en progreso».

### R3-A4. «Completar portadas» se atasca en los mismos 30 ítems [V]

- **Dónde:** `app/services/enrich.py:156` (`order_by(id).limit(30)`).
- **Qué pasa:** los ítems sin coincidencia siguen sin portada y vuelven a ser «los 30 primeros» en cada lote.
- **Efecto:** tras importar 400 libros, si 30 son raros, cada pulsación gasta 90 peticiones de cuota en esos mismos 30, y los otros 370 no se procesan nunca.
- **Arreglo:** guardar un «último intento» (por ejemplo `cover_checked_at`) y ordenar con `NULLS FIRST`.

### R3-A5. La publicación a ghcr no pasa por toda la CI [L]

- **Dónde:** `.github/workflows/docker-publish.yml:36`.
- **Qué pasa:** el job solo hace `needs: tests` de su propio workflow. Un push a `main` publica `latest` aunque estén en rojo `pip-audit`, el smoke de la imagen endurecida o la migración desde una base antigua (jobs de `ci.yml`).
- **Arreglo:** mover la publicación a `ci.yml` con `needs: [tests, auditoria, docker, despliegue]` y la condición `if: github.event_name == 'push'`.

### R3-A6. Contraste del tema claro por debajo de WCAG AA [V]

| Token | Ratio sobre `--bg` | Ratio sobre `--card` | Uso |
|---|---|---|---|
| `--text-3` `#8a7a58` | 3,26 | 3,72 | metadatos, `.subtle`, cabeceras en versalitas, fechas, `th`, vacíos |
| `--warning` `#a3701f` | 3,33 | 3,80 | |
| `--star` `#b07a1c` | 2,89 | 3,30 | lo usa `.stars-num` como texto |

- **Arreglo:**
  - `--text-3: #6b5d40` (4,99 / 5,70).
  - `--warning` en torno a `#7e5810`.
  - Un `--star-text` propio para el número de la nota.
  - En el tema claro, quitar el brillo `+8 %` del `:hover` del botón primario: la parte alta del degradado queda en 4,22.

---

## Severidad media

### Seguridad

| ID | Hallazgo | Arreglo |
|---|---|---|
| R3-M1 [V] | El CSRF acepta `Sec-Fetch-Site: same-site`, que ignora el puerto. Otra app en el mismo host (`:8003`) puede hacer POST, y con `same-site` se ignora un `Origin` ajeno (`csrf.py:44-47`) | Aceptar solo `same-origin` y `none`; el resto, comprobar `Origin` contra la lista |
| R3-M2 [V] | Sin validación de `Host`: con DNS rebinding se entra en una cuenta sin contraseña y se lee y escribe el catálogo. Afecta al modo por defecto, incluso con el bind a 127.0.0.1 | `ALLOWED_HOSTS` + `TrustedHostMiddleware`; el CSRF compara contra esa lista |
| R3-M3 [V] | Sin límite de intentos en `/cuentas/entrar`. Mínimo de 4 caracteres, ~60 ms por intento, sin bloqueo. Lo mismo en Basic Auth | Retardo exponencial por cuenta e IP; mínimo de 8 caracteres para contraseñas nuevas |
| R3-M4 [V] | La sesión no se invalida: tras «salir» o cambiar la contraseña, la cookie vieja sigue valiendo un año. Cookie sin `Secure`, y la clave de sesión va dentro de cada backup | `sesion_version` en `Usuario`; `max_age` menor; `SECURE_COOKIES` configurable |
| R3-M5 [V] | `POST /importar/completar-portadas` y `GET /importar/estado-portadas` no piden cuenta y trabajan sobre todas las cuentas, incluso renombrando títulos ajenos (`imdb_import.py:343-360`) | `Depends(usuario_actual)` y lote acotado a la cuenta |
| R3-M6 [V] | El anti-SSRF del feed de podcasts sigue redirecciones sin revalidar, y deja pasar `100.64.0.0/10` (Tailscale, que el README recomienda) | `not ip.is_global`; seguir redirecciones a mano revalidando cada salto |

### Lógica de datos

| ID | Hallazgo | Arreglo |
|---|---|---|
| R3-M7 [V] | `completed_at` no se borra al salir de «completado». Si marcas «Visto» por error y luego «Por ver», sigue contando en estadísticas, «mejores del año», portada y diario. **Lo introdujo el commit del rediseño** (`metadata.cambiar_estado`) | Poner `completed_at = None` si el nuevo estado no es COMPLETADO |
| R3-M8 [V] | El botón «Visto» en una serie no marca los episodios: queda COMPLETADO con 0/N, y el primer episodio que marques la devuelve a «en progreso» | Al completar una episódica, marcar todos los emitidos |
| R3-M9 [V] | `/agregar` con estado «en progreso» no fija `started_at`, así que no sale en el diario | Usar `cambiar_estado` |
| R3-M10 [V] | Diario: lo empezado en marzo y terminado en abril no aparece en marzo (`actividad.py:101`) | Excluir solo si `completed_at == started_at` |
| R3-M11 [V/L] | Las fechas salen de `date.today()` en UTC (unos 20 sitios) mientras la cabecera usa `TIMEZONE`. Un episodio visto a las 00:30 en Madrid se guarda con el día anterior | `fechas.hoy()` única con `ZoneInfo(settings.timezone)` |
| R3-M12 [V] | `load_episodes` nunca actualiza episodios existentes. Un `air_date` que TMDB fija después no llega nunca, ni al calendario ni a los avisos | Actualizar fecha, nombre y duración; `notified=False` si la fecha pasa a futura |
| R3-M13 [L] | Los podcasts nunca reciben episodios nuevos (el job no refresca RSS), y la numeración por posición se descolocaría si lo hiciera | Refrescar el feed; numerar por `guid` o por fecha |
| R3-M14 [V] | Importadores, falsos duplicados: la clave es solo el título, así que Dune 1984 y Dune 2021 quedan en uno | Clave `(tipo, título normalizado, año)` |
| R3-M15 [V] | Importadores, duplicados reales: IMDb guarda `imdb:tt…` y Trakt `tt…`; `/agregar` no deduplica nada | `external_id` canónico; avisar del posible duplicado en `/agregar` |
| R3-M16 [V] | Importadores, datos perdidos: un duplicado no completa al existente. `ratings.csv` después de `watched.csv` pierde todas las notas, y reimportar Steam no actualiza las horas | Rellenar los campos vacíos del existente (upsert) |
| R3-M17 [V] | StoryGraph `2022/01/05-2022/02/10` se guarda como 2022-01-01; un «read» de Goodreads sin fecha queda completado e invisible | Parsear el rango; fallback a `Date Added` |
| R3-M18 [V] | Recomendaciones: un 1/10 completado cuenta como «te gustó» (`recomendaciones.py:94`) | Ignorar notas ≤ 4 |
| R3-M19 [V] | N+1: la ficha hace una consulta por lista (16 SELECT con 10 listas) y `/calendario` una por serie | Una consulta sobre `list_items`; `joinedload(Episode.item)` |
| R3-M20 [L] | Los importadores son `async def` con SQLAlchemy síncrono: un CSV de 5 000 filas congela la app unos segundos | `run_in_threadpool` o `def` |
| R3-M21 [V] | No hay exportación de datos ni descarga de backup, ni forma de borrar una cuenta | Exportar CSV/JSON y descargar el último backup desde `/estado` |

### Infraestructura y frontend

| ID | Hallazgo | Arreglo |
|---|---|---|
| R3-M22 [V] | No hay ningún tag git: la imagen `:1.0.0` no existe, solo `latest` móvil, y detrás van migraciones de cuentas y géneros | Proceso de release en CONTRIBUTING; cortar la versión siguiente |
| R3-M23 [L] | Actions sin fijar por SHA y sin bloques `permissions:` en `ci.yml` | `contents: read` global; SHAs fijos, que Dependabot ya mantiene |
| R3-M24 [V] | `CONTRIBUTING.md:25` manda hacer los cambios de esquema con `ensure_columns()`, que no es el mecanismo desde MC-M4: un contribuidor rompería las bases existentes | Documentar `alembic revision --autogenerate` |
| R3-M25 [L] | Transitivas sin fijar y sin hashes; `pip-audit -r` solo audita lo directo | `pip-compile --generate-hashes` + `--require-hashes` |
| R3-M26 [V] | Manifest PWA con colores de la paleta clara en una app oscura, y solo un icono de 180 px: Android no ofrece instalarla | Colores oscuros; iconos de 192, 512 y maskable |
| R3-M27 [V] | Flechas de los carruseles enfocables pero invisibles para quien usa teclado | Mostrarlas con `:focus-within` |
| R3-M28 [V] | Objetivos táctiles por debajo de 44 px en móvil (iconos de 33 px, `ep-check` de 26 px, medias estrellas de 13×26) | `min-height: 44px` por debajo de 1024 px |
| R3-M29 [V] | Los resultados de HTMX (sugerencia, tiempo, búsqueda) no se anuncian a lectores de pantalla | `aria-live="polite"` |
| R3-M30 [V] | Sin estilos de impresión: se imprime la barra lateral fija sobre fondo oscuro | `@media print` |

---

## Severidad baja y mejoras

### Seguridad

- **R3-B1** [V] `inf` o `1e999` en `progress_*` u `hltb_hours`, o un año de 30 dígitos, desde un POST fabricado dejan `/`, `/catalogo`, la ficha y `/estadisticas` en 500 de forma persistente. Arreglo: un parser numérico común con `isfinite` y rangos acotados.
- **R3-B2** [V] Importadores: `inf`, campos de más de 128 KB o un rating de 99 dan 500 o datos inválidos. Arreglo: capturar `OverflowError` y `csv.Error`, y pasar el rating por `_parse_rating`.
- **R3-B3** [V] `?buscar=` con más de 1 000 palabras da 500 (árbol de expresión de SQLite). Arreglo: limitar a 10 palabras.
- **R3-B4** [V] `/docs`, `/redoc` y `/openapi.json` están expuestos sin cuenta, y quedan fuera de Basic Auth. Arreglo: desactivarlos.
- **R3-B5** [L] El backup de la base contiene los hashes de las contraseñas y la clave de sesión, con el umask por defecto. Arreglo: `chmod 600`.
- **R3-B6** [L] La CSP mantiene `'unsafe-inline'`: hay tres scripts en línea. Arreglo: moverlos a ficheros y quitarlo.
- **R3-B7** [L] Faltan `Cache-Control: no-store` en las páginas autenticadas y `Permissions-Policy`.

### Datos

- **R3-B8** [V] `/actividad?mes=0001-01` y `?mes=9999-12` dan 500 (`mes_anterior` y `mes_siguiente` desbordan).
- **R3-B9** [V] `PRAGMA foreign_keys` está desactivado y las FK de `usuario_id` no tienen `ondelete`. Además, la base migrada no tiene esas FK y la creada desde los modelos sí (`alembic check` lo detecta: propone dos `add_fk`).
- **R3-B10** [V] Las tablas puente hacen `SCAN` al filtrar por género o etiqueta. Arreglo: índice sobre la segunda columna de cada clave compuesta.
- **R3-B11** [L] Telegram avisa de episodios ya vistos y, al reactivarlo, envía de golpe todo lo acumulado, sin agrupar y sin control de 429. Además, un único chat recibe los avisos de todas las cuentas.
- **R3-B12** [L] Letterboxd `diary.csv` toma `Date` (la fecha del registro) antes que `Watched Date`.
- **R3-B13** [L] Los CSV con `;` (Excel en español) se omiten enteros sin explicar por qué.
- **R3-B14** [L] Los clientes de API devuelven `[]` ante cualquier fallo: no se distingue «sin resultados» de «cuota agotada», y un enriquecimiento fallido no se reintenta.
- **R3-B15** [L] El backup del día se sobrescribe en el sitio: si falla a mitad, se pierde el bueno. Arreglo: escribir en `.tmp`, hacer `os.replace` y `integrity_check`.
- **R3-B16** [L] Código duplicado: tres `_get` y dos `_parse_date` repartidos entre `imports.py` e `imdb_import.py`. `security.py` sigue diciendo «mono-usuario».
- **R3-B17** [V] `/buscar` sin clave configurada dice «escribe al menos 2 caracteres» aunque se hayan escrito más.

### Infraestructura

- **R3-B18** [L] Dockerfile: la base no está fijada por digest y la capa de `apt` va después de `COPY app`, así que se repite en cada cambio de código. Faltan `PYTHONDONTWRITEBYTECODE` y `PYTHONUNBUFFERED`.
- **R3-B19** [L] Dependabot no cubre el ecosistema `docker`.
- **R3-B20** [L] El healthcheck está duplicado en el Dockerfile y en el compose.
- **R3-B21** [V] La cobertura no se mide en CI. Los módulos menos cubiertos son `tmdb.py` (36 %), `hltb.py` (26 %) y `auth.py` (77 %).
- **R3-B22** [V] Ningún test ejercita los `downgrade` de las migraciones, ni la deriva entre los modelos y las migraciones.
- **R3-B23** [V] El README y `test_fallos_conocidos.py` describen tests `xfail` que ya no existen.
- **R3-B24** [V] `docker-entrypoint.sh` y `.github/*.sh` no tienen el bit de ejecución en git.
- **R3-B25** [V] Faltan en el README `TRUSTED_ORIGINS`, `ENABLE_SCHEDULER`, `APP_NAME` y `MEDIA_BIND`.
- **R3-B26** [V] Ruff: 15 `noqa` sobrantes (RUF100). `itunes.py` parsea XML externo sin `defusedxml`.

### Frontend

- **R3-B27** [V] La jerarquía de encabezados salta de `h1` a `h3` en todas las páginas.
- **R3-B28** [V] Las gráficas no tienen alternativa con los datos en texto.
- **R3-B29** [V] El `@view-transition` dentro de `@media (prefers-reduced-motion)` no es válido y no tiene efecto.
- **R3-B30** [V] El asa de la hoja «Más» no es accesible por teclado.
- **R3-B31** [V] Toasts: el aviso puede anunciarse dos veces (`aria-live` en el contenedor y `role` en cada toast) y no se pausan al pasar el ratón.
- **R3-B32** [V] Bordes de los campos de formulario con un contraste de 1,2:1 en reposo (WCAG 1.4.11 pide 3:1).
- **R3-B33** [V] Los avatares con tonos amarillos o verdes dan menos de 3:1.
- **R3-B34** [V] CSS sin usar: `.cover-progress`, `.card-title`, `.section-plain`, `.inline-form` y `.month-nav`.

---

## Orden recomendado

1. **Arreglos de una línea con impacto alto:**
   - R3-A1: silenciar el log de `httpx`.
   - R3-M7: limpiar `completed_at`.
   - R3-M5: pedir cuenta en los dos endpoints de portadas.
   - R3-B4: quitar `/docs`.
   - R3-B8: desbordamiento de `/actividad`.
   - R3-A6: tokens de contraste del tema claro.
2. **Aislamiento y acceso:** R3-A2 (géneros por cuenta), R3-M1, R3-M2 y R3-M3.
3. **Seguimiento de series y podcasts:** R3-A3, R3-M12, R3-M13 y R3-M8. Es lo que más se notará en el uso diario.
4. **CI y release:** R3-A5, R3-M22, R3-M23 y R3-M24.
5. **Importadores:** R3-M14 a R3-M17, junto con el parser numérico común (R3-B1 y R3-B2).
6. **Funcionalidad nueva:** exportar datos y descargar el backup (R3-M21), borrar una cuenta (con R3-B9) y reintentar enriquecimientos (R3-A4 y R3-B14).

Ninguno de estos arreglos requiere dependencias nuevas.

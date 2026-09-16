# Modelo ER para Supabase — Pulso TransMi

Esquema recomendado para PostgreSQL/Supabase. La tabla de hechos es
`demand_observations`; las dimensiones describen estaciones, tiempo, clima y
eventos.

```mermaid
erDiagram
    CORRIDORS ||--o{ STATIONS : contains
    STATIONS ||--o{ DEMAND_OBSERVATIONS : records
    TIME_DIMENSION ||--o{ DEMAND_OBSERVATIONS : timestamps
    TIME_DIMENSION ||--|| WEATHER_CONTEXT : has
    TIME_DIMENSION ||--|| EVENT_CONTEXT : has
    CORRIDORS { int corridor_id PK string name }
    STATIONS { string station_id PK string station_name int corridor_id FK decimal latitude decimal longitude boolean active }
    TIME_DIMENSION { datetime observed_at PK date date_value int year int month int day int hour int minute int day_of_week boolean is_weekend }
    WEATHER_CONTEXT { datetime observed_at PK decimal rain_mm decimal rain_forecast decimal temperature_c decimal temperature_forecast }
    EVENT_CONTEXT { datetime observed_at PK decimal event_intensity }
    DEMAND_OBSERVATIONS { datetime observed_at PK string station_id PK integer demand }
    INGESTION_RUNS { bigint ingestion_id PK string resource_name datetime started_at string status integer rows_loaded }
```

## Tablas

| Tabla | Granularidad | Clave |
|---|---|---|
| `corridors` | Un registro por corredor | `corridor_id` |
| `stations` | Un registro por estación | `station_id` |
| `time_dimension` | Un registro por instante de 15 minutos | `observed_at` |
| `weather_context` | Un registro por instante | `observed_at` |
| `event_context` | Un registro por instante | `observed_at` |
| `demand_observations` | Una estación en un instante | `(observed_at, station_id)` |
| `ingestion_runs` | Una ejecución de carga desde la API | `ingestion_id` |

## Decisiones importantes

- Usar `TIMESTAMPTZ` para `observed_at`.
- Usar `VARCHAR` para `station_id`, conservando ceros iniciales como `03000`.
- La relación entre contexto y demanda se realiza por `observed_at`.
- `demand_observations` debe tener índices por `(station_id, observed_at)` y
  `observed_at`.
- `ingestion_runs` permite auditar cuándo y cuántas filas fueron cargadas.

El SQL completo está descrito en la sección de esquema relacional del modelo
general y puede ejecutarse desde el SQL Editor de Supabase.

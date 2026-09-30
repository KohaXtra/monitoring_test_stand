# FastAPInginx — стенд наблюдаемости

Учебный стенд: FastAPI-приложение за nginx, три источника данных (Loki, Mimir,
Prometheus), Grafana с дашбордами как кодом, нагрузка k6 и «ломалки», чтобы было
что расследовать. Поднимается одной командой.

```
k6 ──> nginx:8080 ──> app:8000 (FastAPI, /metrics)
                          │
   Alloy ── scrape ───────┼──> Mimir:9009   (долгое хранение)
   Alloy ── docker logs ──┼──> Loki:3100
   Prometheus ── scrape ──┘    (6h, локально)
   node-exporter (сеть хоста) ── метрики машины
                 Grafana:3000 — три датасорса, три дашборда
```

## Что внутри

| компонент | зачем |
|---|---|
| **app** (FastAPI) | `/items/{id}`, `/hello/{name}`, `/`, `/boom`; метрики через `prometheus-fastapi-instrumentator`, JSON-лог на каждый запрос с `request_id` |
| **nginx** | прокси перед app, JSON access-лог (`request_time`, `upstream_response_time`), генерирует `X-Request-ID` |
| **Alloy** | скрейп app и node-exporter → Mimir; логи контейнеров стенда → Loki |
| **Mimir** | долгое хранение метрик (монолит, файловое хранилище) |
| **Prometheus** | те же цели, короткое хранение 6 часов — для сравнения с Mimir |
| **Loki** | логи nginx и app (монолит, ретеншн 7 дней) |
| **node-exporter** | метрики машины, в сети хоста |
| **Grafana** | датасорсы и дашборды из provisioning |
| **k6** | фоновая нагрузка ~20 RPS (профиль `load`) |

## Требования

- Docker и Docker Compose v2.
- Linux: node-exporter работает с `network_mode: host`, а Alloy читает
  `/var/run/docker.sock`. На Docker Desktop (macOS/Windows) «хост» — это
  виртуальная машина Docker, и метрики node-exporter будут её, а не вашей машины.
- Свободные порты: 3000, 3100, 8000, 8080, 9009, 9090, 9100, 12345.

## Запуск

```bash
cd monitoring
docker compose up -d --build              # стенд
docker compose --profile load up -d k6    # фоновая нагрузка ~20 RPS
docker compose --profile load stop k6     # остановить нагрузку
docker compose down                       # остановить стенд (данные в volume'ах сохраняются)
```

Занят порт — `cp .env.example .env` и поменяйте нужное значение в `.env`;
конфиги сервисов трогать не нужно. Версии всех образов закреплены в
`docker-compose.yml`.

## Где смотреть

| | |
|---|---|
| Grafana (admin/admin) | http://localhost:3000 |
| Приложение через nginx | http://localhost:8080 |
| Приложение напрямую (`/metrics`, `/chaos`) | http://localhost:8000 |
| Prometheus targets | http://localhost:9090/targets |
| Alloy UI (граф компонентов, ошибки скрейпа) | http://localhost:12345 |

Пароль `admin/admin` задаётся только при первой инициализации пустого volume
`grafana-data`.

## Дашборды

Лежат в `monitoring/grafana/provisioning/dashboards/` и подхватываются
автоматически.

| дашборд | что показывает |
|---|---|
| **RED** | Rate / Errors / Duration по приложению с фильтром по маршруту; p95 из метрик и из логов nginx рядом |
| **PromQL и LogQL: примеры** | формулы с разобранными ловушками: доля ошибок и пустые серии, p95 из гистограммы и из логов, topk в режиме Instant, `or vector(0)`, Expressions, смешанный датасорс |
| **Node Exporter Full (адаптированный 1860)** | импорт [grafana.com/dashboards/1860](https://grafana.com/grafana/dashboards/1860) ([rfmoz/grafana-dashboards](https://github.com/rfmoz/grafana-dashboards)), переделанный под стенд: убраны лишние ряды, переменная хоста показывает только живые хосты |

## Ломалки

Действуют на `/items/{item_id}`, состояние хранится в памяти приложения.

```bash
curl -X POST 'localhost:8000/chaos?error_rate=0.3'   # 30% ответов 500
curl -X POST 'localhost:8000/chaos?delay=0.5'        # +~0.5 с латентности
curl -X POST 'localhost:8000/chaos'                  # починить
curl localhost:8080/boom                             # гарантированное исключение
```

Сломать сбор, а не сервис: `docker compose stop node-exporter` или
`docker compose stop alloy`.

## Сценарий инцидента (~10 минут)

1. Запустить k6, открыть RED: дашборд в норме.
2. `curl -X POST 'localhost:8000/chaos?error_rate=0.2'` — растут Errors и доля 5xx.
3. Explore: `topk` по маршрутам и Expression `$A / $B` — какой маршрут и какой
   статус.
4. Loki за то же окно: `{service="nginx"} | json | status >= 500`, клик по
   `request_id` — строки nginx и app одного запроса.
5. Один и тот же запрос в Prometheus и в Mimir за двое суток: история есть только
   в Mimir.
6. `curl -X POST 'localhost:8000/chaos?error_rate=0'` — возврат к норме.

## Метки в Loki

`service`, `container`, `level` (только для app). Всё остальное (`request_id`,
`route`, `status`, `uri`, `request_time`) — поля внутри JSON-строки, достаются
через `| json`. Почему так — в [THEORY.md](THEORY.md#1-loki-метки-против-содержимого).

## Структура

```
main.py, Dockerfile, requirements.txt      приложение
monitoring/
  docker-compose.yml                       все сервисы, профиль load для k6
  .env.example                             порты на хосте
  alloy/config.alloy                       сбор метрик → Mimir и логов → Loki
  prometheus/prometheus.yml                цели скрейпа Prometheus
  mimir/mimir.yml, loki/loki.yml           монолиты с файловым хранилищем
  nginx/nginx.conf                         прокси, JSON-лог, request_id
  k6/load.js                               профиль нагрузки
  grafana/provisioning/                    датасорсы и дашборды как код
```

## Документация

- [STAND-GUIDE.md](STAND-GUIDE.md) — как устроен стенд: путь запроса, метрики,
  логи, Grafana, как проверить каждое звено, типичные поломки, упрощения
  относительно прода.
- [THEORY.md](THEORY.md) — теория к формулам: `rate`/`irate`/`increase`, сброс
  counter, `$__rate_interval`, перцентили и гистограммы, пустые серии, цена
  лог-метрик, дашборды, алерты.

## Лицензия

[MIT](LICENSE). Дашборд Node Exporter Full основан на работе
[rfmoz/grafana-dashboards](https://github.com/rfmoz/grafana-dashboards) и
распространяется на условиях лицензии оригинала.

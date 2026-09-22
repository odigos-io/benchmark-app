# The test application

The service the benchmark measures. One endpoint, a fixed sequence of database, cache and HTTP
operations, and a CPU knob that changes how much work sits between those operations without
changing how many spans a request produces. It is built with Spring Boot 3.5 on JDK 21, and
talks to Postgres, Redis and a small echo service.

## Request shape

`POST /api/checkout` (GET accepted), synchronous on the Tomcat thread. With the default knobs
(`jdbc_ops=3`, `redis_ops=2`, `http_calls=1`) every request does, in this order:

1. Redis `GET bench:cust:{c}` (static key)
2. JDBC `SELECT ... FROM customers WHERE id = ?` (primary key)
3. JDBC `SELECT ... FROM order_lookup WHERE customer_id = ? AND ... LIMIT 5` (six binds, index range)
4. `CpuWork.run(cpu_units, n)`: no span, pure CPU
5. JDBC `INSERT INTO orders ... ON CONFLICT (slot) DO UPDATE` into a pre-filled ring
6. HTTP `POST {DOWNSTREAM_URL}/echo?delay_ms={d}` with `payload_bytes` of body (RestTemplate over `HttpURLConnection`)
7. Redis `SET bench:last:{c} EX 300` (churn key)

Odigos emits two client spans per outbound HTTP call (RestTemplate, and the `HttpURLConnection`
underneath it), so that is 1 server + 3 JDBC + 2 Redis + 2 HTTP client = **8 spans per request**.
In general: `spans = 1 + jdbc_ops + redis_ops + 2 x http_calls`.
`c = n % 1024` and `slot = (n * 7919) % ring_slots` where `n` is a process-wide request counter,
so the customer walk and the ring walk are deterministic permutations, never random.

Larger op counts cycle the kinds: JDBC ops cycle PK select, range select, upsert; Redis ops
alternate GET and SET; HTTP calls repeat the POST. Reads run before the CPU work, writes after.
The S-chatty preset (`5/4/2`) gives GET, GET, PK, range, PK, range, CPU, upsert, POST, POST,
SET, SET = **14 spans per request** (the two POSTs are four client spans), the shape of a chatty
orchestration service.

### CPU unit

One unit builds a ~6.3 KB `OrderDoc` (header, 32 line items with `BigDecimal` prices, two
attributes), serialises it with Jackson, parses it back, and hashes the bytes with SHA-256. The
digest is folded into the next unit's document and the parsed document feeds the next nonce, so
nothing is dead code. The result is deterministic from `(seed, units)`: the response `checksum`
for a given request counter value is the same on every machine and every arm.

Golden value, pinned in `CpuWorkGoldenTest`:

```
CpuWork.run(10, 1L) = aa410d70ee23a4ec07fe1808b954ef2cc7d71dfc74cd8bc0a4f107274d9f583b
```

If it changes, results are no longer comparable to earlier runs; say so in the report.

## Knobs

Resolution order, highest first:

1. Query parameters (`?cpu_units=&jdbc_ops=&redis_ops=&http_calls=&downstream_delay_ms=&payload_bytes=`),
   honoured only when `BENCH_ALLOW_QUERY_OVERRIDES=true`. Off during measured runs.
2. Environment: `BENCH_CPU_UNITS`, `BENCH_JDBC_OPS`, `BENCH_REDIS_OPS`, `BENCH_HTTP_CALLS`,
   `BENCH_DOWNSTREAM_DELAY_MS`, `BENCH_PAYLOAD_BYTES`.
3. Preset `BENCH_PRESET` (default `S`). Preset CPU is a target in ms converted with
   `BENCH_UNITS_PER_MS` (default 10, overwritten per machine by calibration).

| Preset | cpu target ms | cpu_units @10/ms | delay_ms | jdbc/redis/http | spans/req |
|---|---|---|---|---|---|
| `S` | 3 | 30 | 10 | 3/2/1 | 8 |
| `S_CHATTY` | 4 | 40 | 10 | 5/4/2 | 14 |
| `M` | 12 | 120 | 20 | 3/2/1 | 8 |
| `L` | 30 | 300 | 30 | 3/2/1 | 8 |
| `XL` | 60 | 600 | 40 | 3/2/1 | 8 |

The validation cells in `cells.env` start from a preset and override the operation counts and
CPU units through the environment variables below.

`payload_bytes` defaults to 512 for every preset.

Other environment: `DB_URL`, `DB_USER`, `DB_PASSWORD`, `REDIS_HOST`, `REDIS_PORT`,
`DOWNSTREAM_URL` (default `http://echo:8080`), `BENCH_RING_SLOTS` (default 100000, must be
coprime with 7919), `POD_IP` (used by the self-call guard), `PORT` (default 8080).
JVM flags are not baked into the image; pass them with `JAVA_TOOL_OPTIONS`, including
`-Dhttp.maxConnections=64` for the `HttpURLConnection` keep-alive cache.

## Endpoints

| Endpoint | Purpose |
|---|---|
| `POST/GET /api/checkout` | the measured request; returns `ok`, `preset`, `knobs`, `ops{jdbc,redis,http}` as executed, `checksum`, `app_ms`, `thread_cpu_us` |
| `POST /admin/reset` | `VACUUM (ANALYZE) orders`, reseed Redis static keys, delete churn keys, assert row counts, dead tuples and key counts; 200 or 503 with detail |
| `GET/POST /admin/calibrate?units=100&rounds=200&threads=1` | measure µs per unit with `ThreadMXBean` CPU time (p10/p50/p90, cv, JIT ms during the measured phase, suggested units for 4/12/30/60 ms); 409 while requests are in flight |
| `GET /admin/config` | effective knobs, env overrides, JVM arguments |
| `GET /healthz` | 200 only when Postgres, Redis and the echo service answer |
| `GET /actuator/prometheus` | `bench_requests_total{preset}`, `bench_ops_total{kind}`, `bench_cpu_units_total`, `bench_thread_cpu_seconds_total`, `bench_inflight`, plus JVM GC/allocation/compilation metrics |

`BENCH_MODE=calibrate` runs the calibrator on 1 and 2 threads without Spring, Postgres or
Redis, prints one JSON document and exits 0 (`BENCH_CALIBRATE_UNITS`, `BENCH_CALIBRATE_ROUNDS`).

`thread_cpu_us` is the request thread's CPU only; Lettuce I/O threads, GC and JIT are not in it.
It is a cross-check for the cgroup measurement, not a replacement.

## State and stationarity

`schema.sql` is idempotent and runs on every start. `customers` (1,024 rows) and `order_lookup`
(10,000 rows, index on `(customer_id, created_at DESC)`) are static. `orders` is a ring of
`BENCH_RING_SLOTS` rows pre-filled at startup, primary key `slot`, no index on the updated
columns, `fillfactor = 70`, autovacuum on an absolute dead-tuple threshold with the cost limiter
off. Every upsert is a HOT update; `SchemaStationarityTest` drives 250k upserts through a
10,000-slot ring with 8 connections and asserts the heap, the index and the p50 upsert time stay
flat and that every update is HOT. Redis holds at most 2,048 keys.

## Verifying a deployment

```
curl -s -X POST http://app:8080/api/checkout | jq .
```

Expect `ops` = `{jdbc:3, redis:2, http:1}` (`5/4/2` for S-chatty), `knobs.cpu_units` equal to
the cell's setting, and a 64-hex `checksum`. Spans per request at the sink must be 8.00 ± 0.02
(14.00 for S-chatty); the CPU knob never changes that number because Jackson and SHA-256 are not
instrumented.

```
curl -s -X POST http://app:8080/admin/reset | jq .          # ok=true before every arm
curl -s 'http://app:8080/admin/calibrate?units=100&rounds=200&threads=2' | jq .
```

## Build and test

```
mvn -q -DskipTests package                       # target/bucket-bench.jar
mvn test                                         # golden + linearity
mvn test -Dbench.stationarity=true               # adds the Testcontainers Postgres test
mvn test -Dbench.skipLinearity=true              # on a noisy CI box
docker build --platform linux/amd64 -t <your registry>/benchmark-app/app:<tag> .
```

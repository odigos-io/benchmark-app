package io.odigos.bench.core;

import io.odigos.bench.config.BenchProperties;
import io.odigos.bench.cpu.CpuWork;
import io.odigos.bench.io.DownstreamOps;
import io.odigos.bench.io.RedisOps;
import io.odigos.bench.io.SqlOps;
import io.odigos.bench.metrics.BenchMetrics;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.math.BigDecimal;
import java.util.concurrent.atomic.AtomicLong;
import org.springframework.stereotype.Service;

/**
 * One checkout = reads, CPU work, writes, in a fixed order on the request thread:
 *
 * <pre>
 *   Redis GET x ceil(redis_ops/2)
 *   JDBC SELECT customer by PK / SELECT order_lookup range, cycling, for the non-upsert JDBC ops
 *   CPU work (cpu_units, no span)
 *   JDBC upsert for every third JDBC op
 *   HTTP POST /echo x http_calls
 *   Redis SET EX 300 x floor(redis_ops/2)
 * </pre>
 *
 * With the defaults 3/2/1 that is GET, SELECT PK, SELECT range, CPU, UPSERT, POST, SET: 8 spans
 * including the server span, since Odigos emits two client spans per outbound HTTP call
 * (RestTemplate and the HttpURLConnection under it). With 5/4/2 (S-chatty) it is 14.
 */
@Service
public class CheckoutService {

    private static final ThreadMXBean THREADS = ManagementFactory.getThreadMXBean();
    private static final BigDecimal AMOUNT_STEP = new BigDecimal("0.01");

    private final SqlOps sql;
    private final RedisOps redis;
    private final DownstreamOps downstream;
    private final BenchMetrics metrics;
    private final BenchProperties props;
    private final Knobs baseKnobs;
    private final byte[] basePayload;
    private final AtomicLong counter = new AtomicLong();

    public CheckoutService(SqlOps sql, RedisOps redis, DownstreamOps downstream,
                           BenchMetrics metrics, BenchProperties props) {
        this.sql = sql;
        this.redis = redis;
        this.downstream = downstream;
        this.metrics = metrics;
        this.props = props;
        this.baseKnobs = props.baseKnobs();
        this.basePayload = DownstreamOps.payload(baseKnobs.payloadBytes());
    }

    public Knobs baseKnobs() {
        return baseKnobs;
    }

    public CheckoutResult checkout(Knobs knobs) {
        long wall0 = System.nanoTime();
        long cpu0 = THREADS.getCurrentThreadCpuTime();
        metrics.enter();
        try {
            long n = counter.getAndIncrement();
            int c = (int) (n % SqlOps.CUSTOMERS);
            long slot = SqlOps.slotFor(n, props.getRingSlots());
            int jdbc = 0;
            int redisOps = 0;
            int http = 0;

            int gets = (knobs.redisOps() + 1) / 2;
            int sets = knobs.redisOps() / 2;
            for (int i = 0; i < gets; i++) {
                redis.getStatic(c);
                redisOps++;
            }
            for (int i = 0; i < knobs.jdbcOps(); i++) {
                int kind = i % 3;
                if (kind == 0) {
                    sql.selectCustomer(c);
                    jdbc++;
                } else if (kind == 1) {
                    sql.selectRange(c);
                    jdbc++;
                }
            }

            String checksum = CpuWork.run(knobs.cpuUnits(), n);

            BigDecimal amount = AMOUNT_STEP.multiply(BigDecimal.valueOf(1 + (n % 99_999)));
            for (int i = 2; i < knobs.jdbcOps(); i += 3) {
                sql.upsert(slot, c, checksum, amount);
                jdbc++;
            }
            byte[] payload = knobs.payloadBytes() == basePayload.length
                    ? basePayload : DownstreamOps.payload(knobs.payloadBytes());
            for (int i = 0; i < knobs.httpCalls(); i++) {
                int echoed = downstream.echo(knobs.downstreamDelayMs(), payload);
                if (echoed != payload.length) {
                    throw new IllegalStateException("echo returned " + echoed + " bytes, sent " + payload.length);
                }
                http++;
            }
            for (int i = 0; i < sets; i++) {
                redis.setChurn(c, checksum);
                redisOps++;
            }

            long cpuNanos = THREADS.getCurrentThreadCpuTime() - cpu0;
            metrics.record(jdbc, redisOps, http, knobs.cpuUnits(), cpuNanos);
            return new CheckoutResult(true, props.getPreset().name(), knobs,
                    new CheckoutResult.Ops(jdbc, redisOps, http), checksum,
                    (System.nanoTime() - wall0) / 1_000_000.0, cpuNanos / 1_000L);
        } finally {
            metrics.exit();
        }
    }
}

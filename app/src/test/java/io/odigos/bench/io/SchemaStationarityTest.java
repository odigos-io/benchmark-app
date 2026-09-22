package io.odigos.bench.io;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.zaxxer.hikari.HikariConfig;
import com.zaxxer.hikari.HikariDataSource;
import java.math.BigDecimal;
import java.util.Arrays;
import java.util.concurrent.CyclicBarrier;
import java.util.concurrent.atomic.AtomicLong;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfSystemProperty;
import org.springframework.core.io.ClassPathResource;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.datasource.init.ResourceDatabasePopulator;
import org.testcontainers.containers.PostgreSQLContainer;
import org.testcontainers.junit.jupiter.Container;
import org.testcontainers.junit.jupiter.Testcontainers;

/**
 * The orders ring must not grow and the upsert must not slow down under sustained churn.
 *
 * Uses a 10,000-slot ring so every slot is rewritten 25 times, which is more than a full battery
 * does to the production 100k ring. The heap and the primary-key index are expected to stay at
 * their prefilled size from the first pass; the baseline for the latency comparison is taken
 * after {@link #SETTLE_PASSES} passes so JIT and pgjdbc statement preparation do not distort
 * the first window. Every update after the baseline must be HOT. Needs Docker; enable with
 * {@code -Dbench.stationarity=true}.
 */
@Testcontainers
@EnabledIfSystemProperty(named = "bench.stationarity", matches = "true")
class SchemaStationarityTest {

    private static final int RING_SLOTS = 10_000;
    private static final int THREADS = 8;
    private static final int PASSES = 25;
    private static final int SETTLE_PASSES = 5;
    private static final int CHECKPOINT_EVERY_PASSES = 5;
    private static final int UPSERTS_PER_THREAD = RING_SLOTS * PASSES / THREADS;
    private static final int WINDOW_PER_THREAD = RING_SLOTS * CHECKPOINT_EVERY_PASSES / THREADS;
    private static final int CHECKPOINTS = PASSES / CHECKPOINT_EVERY_PASSES;
    private static final double MAX_GROWTH = 1.10;

    @Container
    static final PostgreSQLContainer<?> POSTGRES = new PostgreSQLContainer<>("postgres:16")
            .withDatabaseName("bench").withUsername("bench").withPassword("bench")
            .withCommand("postgres", "-c", "synchronous_commit=off", "-c", "autovacuum_naptime=5s");

    static HikariDataSource dataSource;
    static JdbcTemplate jdbc;

    @BeforeAll
    static void setUp() {
        HikariConfig cfg = new HikariConfig();
        cfg.setJdbcUrl(POSTGRES.getJdbcUrl());
        cfg.setUsername(POSTGRES.getUsername());
        cfg.setPassword(POSTGRES.getPassword());
        cfg.setMaximumPoolSize(THREADS);
        cfg.setMinimumIdle(THREADS);
        dataSource = new HikariDataSource(cfg);
        jdbc = new JdbcTemplate(dataSource);
        new ResourceDatabasePopulator(new ClassPathResource("schema.sql")).execute(dataSource);
        StateService.prefillRing(jdbc, RING_SLOTS);
    }

    @AfterAll
    static void tearDown() {
        if (dataSource != null) {
            dataSource.close();
        }
    }

    @Test
    void ringStaysFlatUnderChurn() throws InterruptedException {
        SqlOps ops = new SqlOps(jdbc);
        assertEquals(RING_SLOTS, (long) jdbc.queryForObject("SELECT count(*) FROM orders", Long.class));

        long[][] latencies = new long[THREADS][UPSERTS_PER_THREAD];
        long[] heapBytes = new long[CHECKPOINTS + 1];
        long[] indexBytes = new long[CHECKPOINTS + 1];
        long[] hotUpdates = new long[CHECKPOINTS + 1];
        long[] updates = new long[CHECKPOINTS + 1];
        int[] checkpoint = new int[1];
        Runnable sample = () -> {
            int k = checkpoint[0]++;
            heapBytes[k] = scalar("SELECT pg_relation_size('orders')");
            indexBytes[k] = scalar("SELECT pg_indexes_size('orders')");
            updates[k] = scalar("SELECT n_tup_upd FROM pg_stat_user_tables WHERE relname = 'orders'");
            hotUpdates[k] = scalar("SELECT n_tup_hot_upd FROM pg_stat_user_tables WHERE relname = 'orders'");
        };
        sample.run();
        CyclicBarrier barrier = new CyclicBarrier(THREADS, sample);
        AtomicLong counter = new AtomicLong();
        Thread[] workers = new Thread[THREADS];
        Throwable[] failure = new Throwable[1];
        for (int t = 0; t < THREADS; t++) {
            final int slot = t;
            workers[t] = new Thread(() -> {
                try {
                    for (int i = 0; i < UPSERTS_PER_THREAD; i++) {
                        long n = counter.getAndIncrement();
                        long t0 = System.nanoTime();
                        ops.upsert(SqlOps.slotFor(n, RING_SLOTS), (int) (n % SqlOps.CUSTOMERS), "ref-" + n,
                                BigDecimal.valueOf(1 + (n % 99_999), 2));
                        latencies[slot][i] = System.nanoTime() - t0;
                        if ((i + 1) % WINDOW_PER_THREAD == 0) {
                            barrier.await();
                        }
                    }
                } catch (Throwable e) {
                    failure[0] = e;
                }
            }, "upsert-" + t);
            workers[t].start();
        }
        for (Thread w : workers) {
            w.join();
        }
        if (failure[0] != null) {
            throw new AssertionError("upsert worker failed", failure[0]);
        }
        assertEquals(CHECKPOINTS + 1, checkpoint[0], "checkpoints sampled");

        int baseline = SETTLE_PASSES / CHECKPOINT_EVERY_PASSES;
        double[] p50 = new double[CHECKPOINTS];
        for (int k = 0; k < CHECKPOINTS; k++) {
            p50[k] = p50(latencies, k * WINDOW_PER_THREAD, (k + 1) * WINDOW_PER_THREAD);
        }
        StringBuilder curve = new StringBuilder("stationarity: ring=" + RING_SLOTS + " threads=" + THREADS
                + " upserts=" + THREADS * UPSERTS_PER_THREAD + "\n");
        for (int k = 0; k <= CHECKPOINTS; k++) {
            curve.append(String.format("  pass %2d: heap=%d idx=%d upd=%d hot=%d%s%n",
                    k * CHECKPOINT_EVERY_PASSES, heapBytes[k], indexBytes[k], updates[k], hotUpdates[k],
                    k > 0 ? String.format(" p50(prev window)=%.0f us", p50[k - 1] / 1000.0) : ""));
        }
        curve.append(String.format("  heap after settle=%d final=%d (x%.3f); p50 first=%.0f us last=%.0f us (x%.3f)%n",
                heapBytes[baseline], heapBytes[CHECKPOINTS], (double) heapBytes[CHECKPOINTS] / heapBytes[baseline],
                p50[baseline] / 1000.0, p50[CHECKPOINTS - 1] / 1000.0, p50[CHECKPOINTS - 1] / p50[baseline]));
        System.out.print(curve);

        long rows = jdbc.queryForObject("SELECT count(*) FROM orders", Long.class);
        assertEquals(RING_SLOTS, rows, "ring row count changed");
        assertTrue(heapBytes[CHECKPOINTS] <= heapBytes[baseline] * MAX_GROWTH,
                "orders heap grew from " + heapBytes[baseline] + " to " + heapBytes[CHECKPOINTS]);
        assertTrue(indexBytes[CHECKPOINTS] <= indexBytes[baseline] * MAX_GROWTH,
                "orders indexes grew from " + indexBytes[baseline] + " to " + indexBytes[CHECKPOINTS]);
        assertTrue(p50[CHECKPOINTS - 1] <= p50[baseline] * MAX_GROWTH,
                "p50 upsert grew from " + p50[baseline] + " ns to " + p50[CHECKPOINTS - 1] + " ns");
        long measuredUpdates = updates[CHECKPOINTS] - updates[baseline];
        long measuredHot = hotUpdates[CHECKPOINTS] - hotUpdates[baseline];
        assertTrue(measuredHot >= measuredUpdates * 0.95,
                "only " + measuredHot + " of " + measuredUpdates + " updates after settle were HOT");
    }

    private static long scalar(String sql) {
        Long v = jdbc.queryForObject(sql, Long.class);
        return v == null ? -1L : v;
    }

    private static double p50(long[][] latencies, int from, int to) {
        long[] window = new long[latencies.length * (to - from)];
        int pos = 0;
        for (long[] perThread : latencies) {
            System.arraycopy(perThread, from, window, pos, to - from);
            pos += to - from;
        }
        Arrays.sort(window);
        return window[window.length / 2];
    }
}

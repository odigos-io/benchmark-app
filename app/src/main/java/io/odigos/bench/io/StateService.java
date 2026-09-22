package io.odigos.bench.io;

import io.odigos.bench.config.BenchProperties;
import java.sql.Connection;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Statement;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;

/**
 * Owns the state every run starts from: the pre-filled orders ring and the static Redis keys.
 * {@link #reset()} is called by the runner between arms, outside any measurement window, and
 * answers 503 when the database does not look like the one the previous arm measured against.
 */
@Service
public class StateService implements ApplicationRunner {

    public static final long EXPECTED_CUSTOMERS = SqlOps.CUSTOMERS;
    public static final long EXPECTED_LOOKUP_ROWS = 10_000L;
    public static final long MAX_DEAD_TUPLES_AFTER_VACUUM = 1_000L;

    private static final String PREFILL_RING =
            "INSERT INTO orders (slot, customer_id, order_ref, amount, updated_at, version) "
                    + "SELECT g, 'cust-' || (g % 1024), 'seed-' || g, ((g % 9999) + 1)::numeric / 100, "
                    + "TIMESTAMP WITH TIME ZONE '2026-01-01 00:00:00+00', 0 "
                    + "FROM generate_series(0, ? - 1) AS g "
                    + "ON CONFLICT (slot) DO NOTHING";

    private final DataSource dataSource;
    private final JdbcTemplate jdbc;
    private final RedisOps redis;
    private final int ringSlots;

    public StateService(DataSource dataSource, JdbcTemplate jdbc, RedisOps redis, BenchProperties props) {
        this.dataSource = dataSource;
        this.jdbc = jdbc;
        this.redis = redis;
        this.ringSlots = props.getRingSlots();
        if (ringSlots <= 0 || gcd(ringSlots, SqlOps.SLOT_STRIDE) != 1) {
            throw new IllegalStateException("BENCH_RING_SLOTS=" + ringSlots
                    + " must be positive and coprime with the slot stride " + SqlOps.SLOT_STRIDE);
        }
    }

    static long gcd(long a, long b) {
        while (b != 0) {
            long t = a % b;
            a = b;
            b = t;
        }
        return a;
    }

    @Override
    public void run(ApplicationArguments args) {
        prefillRing();
        redis.seedStatic();
        Map<String, Object> state = verify();
        if (!Boolean.TRUE.equals(state.get("ok"))) {
            throw new IllegalStateException("benchmark state is not usable at startup: " + state);
        }
    }

    public void prefillRing() {
        prefillRing(jdbc, ringSlots);
        long rows = count("orders");
        if (rows != ringSlots) {
            throw new IllegalStateException("orders ring holds " + rows + " rows, BENCH_RING_SLOTS="
                    + ringSlots + "; drop the table or match the setting to the existing database");
        }
    }

    static void prefillRing(JdbcTemplate jdbc, int ringSlots) {
        jdbc.update(PREFILL_RING, ringSlots);
    }

    public Map<String, Object> reset() {
        long start = System.nanoTime();
        try (Connection conn = dataSource.getConnection()) {
            boolean previous = conn.getAutoCommit();
            conn.setAutoCommit(true);
            try (Statement st = conn.createStatement()) {
                st.execute("VACUUM (ANALYZE) orders");
            } finally {
                conn.setAutoCommit(previous);
            }
        } catch (SQLException e) {
            throw new IllegalStateException("VACUUM (ANALYZE) orders failed", e);
        }
        redis.seedStatic();
        long deletedChurn = redis.deleteChurn();
        Map<String, Object> out = verify();
        out.put("redis_churn_deleted", deletedChurn);
        out.put("took_ms", (System.nanoTime() - start) / 1_000_000L);
        return out;
    }

    public Map<String, Object> verify() {
        Map<String, Object> out = new LinkedHashMap<>();
        long customers = count("customers");
        long lookup = count("order_lookup");
        long ring = count("orders");
        long deadTuples = deadTuples("orders");
        long redisStatic = redis.countStatic();
        boolean ok = customers == EXPECTED_CUSTOMERS
                && lookup == EXPECTED_LOOKUP_ROWS
                && ring == ringSlots
                && deadTuples <= MAX_DEAD_TUPLES_AFTER_VACUUM
                && redisStatic == EXPECTED_CUSTOMERS;
        out.put("ok", ok);
        out.put("customers_rows", customers);
        out.put("expected_customers_rows", EXPECTED_CUSTOMERS);
        out.put("order_lookup_rows", lookup);
        out.put("expected_order_lookup_rows", EXPECTED_LOOKUP_ROWS);
        out.put("orders_rows", ring);
        out.put("expected_orders_rows", (long) ringSlots);
        out.put("orders_dead_tuples", deadTuples);
        out.put("max_dead_tuples", MAX_DEAD_TUPLES_AFTER_VACUUM);
        out.put("orders_relation_bytes", relationSize("orders"));
        out.put("orders_index_bytes", indexesSize("orders"));
        out.put("redis_static_keys", redisStatic);
        if (!ok) {
            out.put("detail", "database or redis does not match the seeded state; "
                    + "results from this deployment are not comparable to other arms");
        }
        return out;
    }

    public boolean databaseReachable() {
        try {
            Integer one = jdbc.queryForObject("SELECT 1", Integer.class);
            return one != null && one == 1;
        } catch (RuntimeException e) {
            return false;
        }
    }

    public long relationSize(String table) {
        Long v = jdbc.queryForObject("SELECT pg_relation_size(?)", Long.class, table);
        return v == null ? -1L : v;
    }

    public long indexesSize(String table) {
        Long v = jdbc.queryForObject("SELECT pg_indexes_size(?::regclass)", Long.class, table);
        return v == null ? -1L : v;
    }

    private long count(String table) {
        Long v = jdbc.queryForObject("SELECT count(*) FROM " + table, Long.class);
        return v == null ? -1L : v;
    }

    private long deadTuples(String table) {
        Long v = jdbc.query("SELECT n_dead_tup FROM pg_stat_user_tables WHERE relname = ?",
                (ResultSet rs) -> rs.next() ? rs.getLong(1) : -1L, table);
        return v == null ? -1L : v;
    }
}

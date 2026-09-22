package io.odigos.bench.io;

import java.math.BigDecimal;
import java.sql.ResultSet;
import java.sql.Timestamp;
import java.time.Instant;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowCallbackHandler;
import org.springframework.stereotype.Component;

/**
 * The three JDBC statement kinds. Every statement is a PreparedStatement with bind parameters so
 * pgjdbc's statement cache and server-side prepare behave as they do in a production service.
 */
@Component
public class SqlOps {

    public static final int CUSTOMERS = 1024;

    /**
     * Consecutive requests are mapped to slots this far apart so concurrent upserts land on
     * different heap pages; the walk is a permutation of the ring when the stride is coprime with
     * BENCH_RING_SLOTS, which {@link StateService} enforces at startup.
     */
    public static final long SLOT_STRIDE = 7919L;

    static final String SELECT_CUSTOMER =
            "SELECT id, name, tier, email, credit_limit FROM customers WHERE id = ?";

    static final String SELECT_RANGE =
            "SELECT id, order_id, amount FROM order_lookup "
                    + "WHERE customer_id = ? AND quantity >= ? AND amount <= ? "
                    + "AND created_at >= ? AND active = ? AND id <> ? "
                    + "ORDER BY created_at DESC LIMIT 5";

    static final String UPSERT_ORDER =
            "INSERT INTO orders (slot, customer_id, order_ref, amount, updated_at) "
                    + "VALUES (?, ?, ?, ?, ?) "
                    + "ON CONFLICT (slot) DO UPDATE SET customer_id = EXCLUDED.customer_id, "
                    + "order_ref = EXCLUDED.order_ref, amount = EXCLUDED.amount, "
                    + "updated_at = EXCLUDED.updated_at, version = orders.version + 1";

    private static final BigDecimal MAX_AMOUNT = new BigDecimal("100000.00");
    private static final Timestamp EPOCH = Timestamp.from(Instant.EPOCH);
    private static final String[] CUSTOMER_IDS = new String[CUSTOMERS];

    static {
        for (int i = 0; i < CUSTOMERS; i++) {
            CUSTOMER_IDS[i] = "cust-" + i;
        }
    }

    private final JdbcTemplate jdbc;

    public SqlOps(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    public static String customerId(int c) {
        return CUSTOMER_IDS[c];
    }

    public static long slotFor(long n, int ringSlots) {
        return (n * SLOT_STRIDE) % ringSlots;
    }

    /** Primary-key point read. Returns the customer's credit limit so the row is consumed. */
    public BigDecimal selectCustomer(int c) {
        return jdbc.query(SELECT_CUSTOMER, rs -> rs.next() ? rs.getBigDecimal(5) : null, c);
    }

    /** Index range scan with six mixed-type binds and LIMIT 5; returns the rows read. */
    public int selectRange(int c) {
        int[] rows = new int[1];
        RowCallbackHandler consume = (ResultSet rs) -> {
            rs.getLong(1);
            rows[0]++;
        };
        jdbc.query(SELECT_RANGE, consume, CUSTOMER_IDS[c], 0, MAX_AMOUNT, EPOCH, Boolean.TRUE, -1L);
        return rows[0];
    }

    /** Upsert into the pre-filled ring; the slot always exists so this is a HOT update. */
    public int upsert(long slot, int c, String orderRef, BigDecimal amount) {
        return jdbc.update(UPSERT_ORDER, slot, CUSTOMER_IDS[c], orderRef, amount,
                new Timestamp(System.currentTimeMillis()));
    }
}

package io.odigos.bench.io;

import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Component;

/**
 * Keyspace is bounded to 2 x 1024 keys: {@code bench:cust:{c}} static, seeded at startup and on
 * reset; {@code bench:last:{c}} churn, written with a 300 s TTL.
 */
@Component
public class RedisOps {

    public static final Duration CHURN_TTL = Duration.ofSeconds(300);
    private static final String[] STATIC_KEYS = new String[SqlOps.CUSTOMERS];
    private static final String[] CHURN_KEYS = new String[SqlOps.CUSTOMERS];

    static {
        for (int i = 0; i < SqlOps.CUSTOMERS; i++) {
            STATIC_KEYS[i] = "bench:cust:" + i;
            CHURN_KEYS[i] = "bench:last:" + i;
        }
    }

    private final StringRedisTemplate redis;

    public RedisOps(StringRedisTemplate redis) {
        this.redis = redis;
    }

    public String getStatic(int c) {
        return redis.opsForValue().get(STATIC_KEYS[c]);
    }

    public void setChurn(int c, String value) {
        redis.opsForValue().set(CHURN_KEYS[c], value, CHURN_TTL);
    }

    public String ping() {
        return redis.execute(connection -> connection.ping(), true);
    }

    public void seedStatic() {
        Map<String, String> values = new LinkedHashMap<>(SqlOps.CUSTOMERS * 2);
        for (int c = 0; c < SqlOps.CUSTOMERS; c++) {
            values.put(STATIC_KEYS[c], staticValue(c));
        }
        redis.opsForValue().multiSet(values);
    }

    public long deleteChurn() {
        List<String> keys = new ArrayList<>(SqlOps.CUSTOMERS);
        for (String k : CHURN_KEYS) {
            keys.add(k);
        }
        Long deleted = redis.delete(keys);
        return deleted == null ? 0L : deleted;
    }

    public long countStatic() {
        List<String> keys = new ArrayList<>(SqlOps.CUSTOMERS);
        for (String k : STATIC_KEYS) {
            keys.add(k);
        }
        Long n = redis.countExistingKeys(keys);
        return n == null ? 0L : n;
    }

    static String staticValue(int c) {
        return "{\"id\":" + c + ",\"name\":\"" + SqlOps.customerId(c)
                + "\",\"tier\":\"" + (c % 3 == 0 ? "gold" : "silver") + "\",\"region\":\"us-east-1\"}";
    }
}

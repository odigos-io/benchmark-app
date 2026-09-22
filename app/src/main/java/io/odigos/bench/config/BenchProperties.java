package io.odigos.bench.config;

import io.odigos.bench.core.Knobs;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.context.properties.ConfigurationProperties;

/**
 * Knob resolution: env {@code BENCH_*} (bound through application.yml) overrides the preset;
 * query parameters override both, but only when {@code BENCH_ALLOW_QUERY_OVERRIDES=true}.
 * Preset CPU is expressed as a target of milliseconds per request and converted to units with
 * {@code BENCH_UNITS_PER_MS}, which calibration overwrites per machine.
 */
@ConfigurationProperties(prefix = "bench")
public class BenchProperties {

    public enum Preset {
        S(3, 10, 3, 2, 1),
        S_CHATTY(4, 10, 5, 4, 2),
        M(12, 20, 3, 2, 1),
        L(30, 30, 3, 2, 1),
        XL(60, 40, 3, 2, 1);

        public final int cpuMs;
        public final int downstreamDelayMs;
        public final int jdbcOps;
        public final int redisOps;
        public final int httpCalls;

        Preset(int cpuMs, int downstreamDelayMs, int jdbcOps, int redisOps, int httpCalls) {
            this.cpuMs = cpuMs;
            this.downstreamDelayMs = downstreamDelayMs;
            this.jdbcOps = jdbcOps;
            this.redisOps = redisOps;
            this.httpCalls = httpCalls;
        }
    }

    public static final int DEFAULT_PAYLOAD_BYTES = 512;

    private Preset preset = Preset.S;
    private boolean allowQueryOverrides = false;
    private int unitsPerMs = 10;
    private int ringSlots = 100_000;
    private String downstreamUrl = "http://echo:8080";
    private Integer cpuUnits;
    private Integer jdbcOps;
    private Integer redisOps;
    private Integer httpCalls;
    private Integer downstreamDelayMs;
    private Integer payloadBytes;

    public Knobs baseKnobs() {
        return new Knobs(
                cpuUnits != null ? cpuUnits : preset.cpuMs * unitsPerMs,
                jdbcOps != null ? jdbcOps : preset.jdbcOps,
                redisOps != null ? redisOps : preset.redisOps,
                httpCalls != null ? httpCalls : preset.httpCalls,
                downstreamDelayMs != null ? downstreamDelayMs : preset.downstreamDelayMs,
                payloadBytes != null ? payloadBytes : DEFAULT_PAYLOAD_BYTES);
    }

    public Map<String, Object> describe() {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("preset", preset.name());
        out.put("allow_query_overrides", allowQueryOverrides);
        out.put("units_per_ms", unitsPerMs);
        out.put("ring_slots", ringSlots);
        out.put("downstream_url", downstreamUrl);
        Map<String, Object> env = new LinkedHashMap<>();
        env.put("cpu_units", cpuUnits);
        env.put("jdbc_ops", jdbcOps);
        env.put("redis_ops", redisOps);
        env.put("http_calls", httpCalls);
        env.put("downstream_delay_ms", downstreamDelayMs);
        env.put("payload_bytes", payloadBytes);
        out.put("env_overrides", env);
        out.put("effective", baseKnobs());
        return out;
    }

    public Preset getPreset() {
        return preset;
    }

    public void setPreset(Preset preset) {
        this.preset = preset;
    }

    public boolean isAllowQueryOverrides() {
        return allowQueryOverrides;
    }

    public void setAllowQueryOverrides(boolean allowQueryOverrides) {
        this.allowQueryOverrides = allowQueryOverrides;
    }

    public int getUnitsPerMs() {
        return unitsPerMs;
    }

    public void setUnitsPerMs(int unitsPerMs) {
        this.unitsPerMs = unitsPerMs;
    }

    public int getRingSlots() {
        return ringSlots;
    }

    public void setRingSlots(int ringSlots) {
        this.ringSlots = ringSlots;
    }

    public String getDownstreamUrl() {
        return downstreamUrl;
    }

    public void setDownstreamUrl(String downstreamUrl) {
        this.downstreamUrl = downstreamUrl;
    }

    public Integer getCpuUnits() {
        return cpuUnits;
    }

    public void setCpuUnits(Integer cpuUnits) {
        this.cpuUnits = cpuUnits;
    }

    public Integer getJdbcOps() {
        return jdbcOps;
    }

    public void setJdbcOps(Integer jdbcOps) {
        this.jdbcOps = jdbcOps;
    }

    public Integer getRedisOps() {
        return redisOps;
    }

    public void setRedisOps(Integer redisOps) {
        this.redisOps = redisOps;
    }

    public Integer getHttpCalls() {
        return httpCalls;
    }

    public void setHttpCalls(Integer httpCalls) {
        this.httpCalls = httpCalls;
    }

    public Integer getDownstreamDelayMs() {
        return downstreamDelayMs;
    }

    public void setDownstreamDelayMs(Integer downstreamDelayMs) {
        this.downstreamDelayMs = downstreamDelayMs;
    }

    public Integer getPayloadBytes() {
        return payloadBytes;
    }

    public void setPayloadBytes(Integer payloadBytes) {
        this.payloadBytes = payloadBytes;
    }
}

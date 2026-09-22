package io.odigos.bench.core;

public record Knobs(
        int cpuUnits,
        int jdbcOps,
        int redisOps,
        int httpCalls,
        int downstreamDelayMs,
        int payloadBytes) {

    public Knobs {
        if (cpuUnits < 0 || jdbcOps < 0 || redisOps < 0 || httpCalls < 0
                || downstreamDelayMs < 0 || payloadBytes < 0) {
            throw new IllegalArgumentException("knobs must be non-negative: " + cpuUnits + "/"
                    + jdbcOps + "/" + redisOps + "/" + httpCalls + "/" + downstreamDelayMs + "/" + payloadBytes);
        }
    }

    public Knobs with(Integer cpuUnits, Integer jdbcOps, Integer redisOps, Integer httpCalls,
                      Integer downstreamDelayMs, Integer payloadBytes) {
        if (cpuUnits == null && jdbcOps == null && redisOps == null && httpCalls == null
                && downstreamDelayMs == null && payloadBytes == null) {
            return this;
        }
        return new Knobs(
                cpuUnits != null ? cpuUnits : this.cpuUnits,
                jdbcOps != null ? jdbcOps : this.jdbcOps,
                redisOps != null ? redisOps : this.redisOps,
                httpCalls != null ? httpCalls : this.httpCalls,
                downstreamDelayMs != null ? downstreamDelayMs : this.downstreamDelayMs,
                payloadBytes != null ? payloadBytes : this.payloadBytes);
    }
}

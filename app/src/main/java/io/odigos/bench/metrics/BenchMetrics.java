package io.odigos.bench.metrics;

import io.micrometer.core.instrument.Counter;
import io.micrometer.core.instrument.Gauge;
import io.micrometer.core.instrument.MeterRegistry;
import io.odigos.bench.config.BenchProperties;
import java.util.concurrent.atomic.AtomicInteger;
import org.springframework.stereotype.Component;

@Component
public class BenchMetrics {

    private final Counter requests;
    private final Counter jdbcOps;
    private final Counter redisOps;
    private final Counter httpOps;
    private final Counter cpuUnits;
    private final Counter threadCpuSeconds;
    private final AtomicInteger inflight = new AtomicInteger();

    public BenchMetrics(MeterRegistry registry, BenchProperties props) {
        String preset = props.getPreset().name();
        requests = Counter.builder("bench.requests").tag("preset", preset).register(registry);
        jdbcOps = Counter.builder("bench.ops").tag("kind", "jdbc").register(registry);
        redisOps = Counter.builder("bench.ops").tag("kind", "redis").register(registry);
        httpOps = Counter.builder("bench.ops").tag("kind", "http").register(registry);
        cpuUnits = Counter.builder("bench.cpu.units").register(registry);
        threadCpuSeconds = Counter.builder("bench.thread.cpu.seconds").baseUnit("seconds").register(registry);
        Gauge.builder("bench.inflight", inflight, AtomicInteger::get).register(registry);
    }

    public int enter() {
        return inflight.incrementAndGet();
    }

    public void exit() {
        inflight.decrementAndGet();
    }

    public int inflight() {
        return inflight.get();
    }

    public void record(int jdbc, int redis, int http, int units, long threadCpuNanos) {
        requests.increment();
        jdbcOps.increment(jdbc);
        redisOps.increment(redis);
        httpOps.increment(http);
        cpuUnits.increment(units);
        threadCpuSeconds.increment(threadCpuNanos / 1_000_000_000.0);
    }
}

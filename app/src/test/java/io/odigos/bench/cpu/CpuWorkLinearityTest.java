package io.odigos.bench.cpu;

import static org.junit.jupiter.api.Assertions.assertTrue;

import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.util.Arrays;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.DisabledIfSystemProperty;

/**
 * cost(100 units) / cost(10 units) must sit in [9, 11] once the JIT has settled: the knob is
 * linear, so a bucket's CPU per request can be dialled by units alone. Skip on a noisy CI box with
 * {@code -Dbench.skipLinearity=true}.
 */
@DisabledIfSystemProperty(named = "bench.skipLinearity", matches = "true")
class CpuWorkLinearityTest {

    private static final ThreadMXBean THREADS = ManagementFactory.getThreadMXBean();
    private static final int WARMUP_ROUNDS = 300;
    private static final int MEASURED_ROUNDS = 60;

    @Test
    void costIsLinearInUnits() {
        long sink = 0;
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            sink += CpuWork.run(10, r).hashCode();
            sink += CpuWork.run(100, r).hashCode();
        }
        double[] small = new double[MEASURED_ROUNDS];
        double[] large = new double[MEASURED_ROUNDS];
        for (int r = 0; r < MEASURED_ROUNDS; r++) {
            small[r] = cpuNanos(10, 10_000 + r);
            large[r] = cpuNanos(100, 20_000 + r);
            sink += (long) small[r] + (long) large[r];
        }
        Arrays.sort(small);
        Arrays.sort(large);
        double small50 = small[MEASURED_ROUNDS / 2];
        double large50 = large[MEASURED_ROUNDS / 2];
        double ratio = large50 / small50;
        System.out.printf("linearity: p50 cpu 10u=%.1f us, 100u=%.1f us, ratio=%.3f, us/unit=%.2f (sink %d)%n",
                small50 / 1000.0, large50 / 1000.0, ratio, large50 / 100_000.0, sink & 1);
        assertTrue(ratio >= 9.0 && ratio <= 11.0, "ratio " + ratio + " outside [9, 11]");
    }

    private static double cpuNanos(int units, long seed) {
        long c0 = THREADS.getCurrentThreadCpuTime();
        int h = CpuWork.run(units, seed).hashCode();
        long c1 = THREADS.getCurrentThreadCpuTime();
        return (c1 - c0) + (h & 1) * 0.0;
    }
}

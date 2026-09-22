package io.odigos.bench.cpu;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.IOException;
import java.io.UncheckedIOException;
import java.lang.management.CompilationMXBean;
import java.lang.management.ManagementFactory;
import java.lang.management.ThreadMXBean;
import java.math.BigDecimal;
import java.math.RoundingMode;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;

/**
 * The CPU primitive. One unit = build an OrderDoc from (seed, unitIndex, previous digest), Jackson
 * write + read, SHA-256 over the bytes. The digest is folded into the next unit's document and the
 * parsed document's contents feed the next nonce, so neither the serialiser, the parser nor the
 * hash can be eliminated. Fully deterministic: no Random, no clock.
 */
public final class CpuWork {

    public static final int LINES = 32;
    private static final int TARGET_MS_S = 4;
    private static final int TARGET_MS_M = 12;
    private static final int TARGET_MS_L = 30;
    private static final int TARGET_MS_XL = 60;
    private static final int WARMUP_ROUNDS = 100;
    private static final ObjectMapper MAPPER = new ObjectMapper();
    private static final char[] HEX = "0123456789abcdef".toCharArray();
    private static final String[] CURRENCIES = {"USD", "EUR", "GBP", "CAD"};
    private static final String[] STATUSES = {"CREATED", "PAID", "PICKED", "SHIPPED"};
    private static final String[] CHANNELS = {"web", "app", "store", "partner"};
    private static final String DESCRIPTION_BASE =
            "Assorted household item, pack of twelve, ships from regional warehouse, colour variant ";
    private static final ThreadMXBean THREADS = ManagementFactory.getThreadMXBean();
    private static final CompilationMXBean COMPILATION = ManagementFactory.getCompilationMXBean();

    private CpuWork() {
    }

    /** Runs {@code units} units chained from {@code seed} and returns the final digest as hex. */
    public static String run(int units, long seed) {
        MessageDigest md = sha256();
        byte[] digest = seedDigest(md, seed);
        for (int i = 0; i < units; i++) {
            digest = oneUnit(md, seed, i, digest);
        }
        return hex(digest);
    }

    /** Serialised size in bytes of the document for the given seed, for documentation and tests. */
    public static int documentBytes(long seed) {
        MessageDigest md = sha256();
        try {
            return MAPPER.writeValueAsBytes(document(seed, 0, seedDigest(md, seed))).length;
        } catch (JsonProcessingException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static byte[] oneUnit(MessageDigest md, long seed, int unitIndex, byte[] prev) {
        OrderDoc doc = document(seed, unitIndex, prev);
        byte[] json;
        OrderDoc parsed;
        try {
            json = MAPPER.writeValueAsBytes(doc);
            parsed = MAPPER.readValue(json, OrderDoc.class);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
        md.update(json);
        md.update((byte) parsed.lines().size());
        md.update((byte) parsed.total().signum());
        return md.digest();
    }

    private static OrderDoc document(long seed, int unitIndex, byte[] prev) {
        long a = readLong(prev, 0);
        long b = readLong(prev, 8);
        long nonce = a ^ (seed * 0x9E3779B97F4A7C15L) ^ unitIndex;
        int variant = (int) (b & 3);
        List<OrderDoc.Line> lines = new ArrayList<>(LINES);
        BigDecimal subtotal = BigDecimal.ZERO;
        for (int i = 0; i < LINES; i++) {
            int qty = 1 + (int) ((b >>> (i & 31)) & 7);
            long cents = 100 + ((a >>> (i & 31)) & 0x3FFF);
            BigDecimal unitPrice = BigDecimal.valueOf(cents, 2);
            BigDecimal lineTotal = unitPrice.multiply(BigDecimal.valueOf(qty));
            subtotal = subtotal.add(lineTotal);
            lines.add(new OrderDoc.Line(
                    i + 1,
                    "SKU-" + ((a + i * 7919L) & 0xFFFFFF),
                    DESCRIPTION_BASE + (i % 8),
                    qty,
                    unitPrice,
                    lineTotal));
        }
        BigDecimal tax = subtotal.multiply(BigDecimal.valueOf(875, 4)).setScale(2, RoundingMode.HALF_UP);
        Map<String, String> attributes = new LinkedHashMap<>(4);
        attributes.put("campaign", "cmp-" + (b & 0xFFFF));
        attributes.put("warehouse", "wh-" + (int) ((a >>> 16) & 0xFF));
        return new OrderDoc(
                hex(prev, 8),
                "cust-" + (a & 1023),
                nonce,
                1_700_000_000_000L + (b & 0xFFFFFFFFL),
                CURRENCIES[variant],
                STATUSES[(int) ((b >>> 2) & 3)],
                CHANNELS[(int) ((b >>> 4) & 3)],
                lines,
                attributes,
                subtotal,
                tax,
                subtotal.add(tax));
    }

    /**
     * Calibration: {@code threads} concurrent workers each run {@link #WARMUP_ROUNDS} discarded
     * rounds then {@code rounds} measured rounds of {@code units} units. Per-round CPU time comes
     * from {@link ThreadMXBean#getCurrentThreadCpuTime()}; wall time from {@link System#nanoTime()}.
     */
    public static Map<String, Object> measure(int units, int rounds, int threads) {
        if (units <= 0 || rounds <= 0 || threads <= 0) {
            throw new IllegalArgumentException("units, rounds and threads must be positive");
        }
        double[][] cpuUsPerUnit = new double[threads][];
        double[][] wallUsPerUnit = new double[threads][];
        CountDownLatch warmed = new CountDownLatch(threads);
        CountDownLatch measure = new CountDownLatch(1);
        Thread[] workers = new Thread[threads];
        Throwable[] failure = new Throwable[1];
        for (int t = 0; t < threads; t++) {
            final int slot = t;
            workers[t] = new Thread(() -> {
                try {
                    double[][] r = measureOnCurrentThread(units, rounds, 1_000L * slot, warmed, measure);
                    cpuUsPerUnit[slot] = r[0];
                    wallUsPerUnit[slot] = r[1];
                } catch (Throwable e) {
                    failure[0] = e;
                    warmed.countDown();
                }
            }, "bench-calibrate-" + t);
            workers[t].start();
        }
        long compileMsBefore;
        try {
            warmed.await();
            compileMsBefore = compileMs();
            measure.countDown();
            for (Thread w : workers) {
                w.join();
            }
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("interrupted during calibration", e);
        }
        if (failure[0] != null) {
            throw new IllegalStateException("calibration worker failed", failure[0]);
        }
        long compileMsAfter = compileMs();

        double[] cpu = flatten(cpuUsPerUnit);
        double[] wall = flatten(wallUsPerUnit);
        Arrays.sort(cpu);
        Arrays.sort(wall);
        double p50 = percentile(cpu, 0.50);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("units", units);
        out.put("rounds", rounds);
        out.put("threads", threads);
        out.put("warmup_rounds", WARMUP_ROUNDS);
        out.put("cpu_us_per_unit_p10", round3(percentile(cpu, 0.10)));
        out.put("cpu_us_per_unit_p50", round3(p50));
        out.put("cpu_us_per_unit_p90", round3(percentile(cpu, 0.90)));
        out.put("cpu_us_per_unit_cv", round3(cv(cpu)));
        out.put("wall_us_per_unit_p50", round3(percentile(wall, 0.50)));
        out.put("units_per_ms", round3(1000.0 / p50));
        out.put("jit_compile_ms_delta", compileMsBefore < 0 ? null : compileMsAfter - compileMsBefore);
        Map<String, Object> suggested = new LinkedHashMap<>();
        suggested.put("S", unitsFor(TARGET_MS_S, p50));
        suggested.put("M", unitsFor(TARGET_MS_M, p50));
        suggested.put("L", unitsFor(TARGET_MS_L, p50));
        suggested.put("XL", unitsFor(TARGET_MS_XL, p50));
        out.put("suggested_units", suggested);
        return out;
    }

    private static double[][] measureOnCurrentThread(int units, int rounds, long seedBase,
                                                     CountDownLatch warmed, CountDownLatch measure)
            throws InterruptedException {
        if (!THREADS.isCurrentThreadCpuTimeSupported()) {
            throw new IllegalStateException("thread CPU time is not supported on this JVM");
        }
        long sink = 0;
        for (int r = 0; r < WARMUP_ROUNDS; r++) {
            sink += run(units, seedBase + r).hashCode();
        }
        warmed.countDown();
        measure.await();
        double[] cpu = new double[rounds];
        double[] wall = new double[rounds];
        for (int r = 0; r < rounds; r++) {
            long c0 = THREADS.getCurrentThreadCpuTime();
            long w0 = System.nanoTime();
            sink += run(units, seedBase + WARMUP_ROUNDS + r).hashCode();
            long w1 = System.nanoTime();
            long c1 = THREADS.getCurrentThreadCpuTime();
            cpu[r] = (c1 - c0) / 1000.0 / units;
            wall[r] = (w1 - w0) / 1000.0 / units;
        }
        if (sink == 42) {
            System.err.println("calibration sink " + sink);
        }
        return new double[][] {cpu, wall};
    }

    private static long compileMs() {
        return COMPILATION.isCompilationTimeMonitoringSupported() ? COMPILATION.getTotalCompilationTime() : -1;
    }

    private static long unitsFor(int targetMs, double usPerUnit) {
        return Math.max(1L, Math.round(targetMs * 1000.0 / usPerUnit));
    }

    private static double[] flatten(double[][] parts) {
        int n = 0;
        for (double[] p : parts) {
            n += p.length;
        }
        double[] all = new double[n];
        int pos = 0;
        for (double[] p : parts) {
            System.arraycopy(p, 0, all, pos, p.length);
            pos += p.length;
        }
        return all;
    }

    private static double percentile(double[] sorted, double q) {
        if (sorted.length == 0) {
            return Double.NaN;
        }
        int idx = (int) Math.min(sorted.length - 1, Math.max(0, Math.round(q * (sorted.length - 1))));
        return sorted[idx];
    }

    private static double cv(double[] values) {
        double mean = 0;
        for (double v : values) {
            mean += v;
        }
        mean /= values.length;
        double var = 0;
        for (double v : values) {
            var += (v - mean) * (v - mean);
        }
        return mean == 0 ? Double.NaN : Math.sqrt(var / values.length) / mean;
    }

    private static double round3(double v) {
        return Math.round(v * 1000.0) / 1000.0;
    }

    private static MessageDigest sha256() {
        try {
            return MessageDigest.getInstance("SHA-256");
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static byte[] seedDigest(MessageDigest md, long seed) {
        byte[] b = new byte[8];
        for (int i = 0; i < 8; i++) {
            b[i] = (byte) (seed >>> (8 * i));
        }
        return md.digest(b);
    }

    private static long readLong(byte[] b, int off) {
        long v = 0;
        for (int i = 0; i < 8; i++) {
            v |= (b[off + i] & 0xFFL) << (8 * i);
        }
        return v;
    }

    private static String hex(byte[] b) {
        return hex(b, b.length);
    }

    private static String hex(byte[] b, int len) {
        char[] out = new char[len * 2];
        for (int i = 0; i < len; i++) {
            out[2 * i] = HEX[(b[i] >>> 4) & 0xF];
            out[2 * i + 1] = HEX[b[i] & 0xF];
        }
        return new String(out);
    }
}

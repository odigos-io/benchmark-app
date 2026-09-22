package io.odigos.bench.core;

public record CheckoutResult(
        boolean ok,
        String preset,
        Knobs knobs,
        Ops ops,
        String checksum,
        double appMs,
        long threadCpuUs) {

    public record Ops(int jdbc, int redis, int http) {
    }
}

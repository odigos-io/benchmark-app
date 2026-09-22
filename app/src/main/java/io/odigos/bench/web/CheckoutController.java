package io.odigos.bench.web;

import io.odigos.bench.config.BenchProperties;
import io.odigos.bench.core.CheckoutResult;
import io.odigos.bench.core.CheckoutService;
import io.odigos.bench.core.Knobs;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class CheckoutController {

    private final CheckoutService service;
    private final boolean allowOverrides;

    public CheckoutController(CheckoutService service, BenchProperties props) {
        this.service = service;
        this.allowOverrides = props.isAllowQueryOverrides();
    }

    @PostMapping("/api/checkout")
    public CheckoutResult post(@RequestParam(name = "cpu_units", required = false) Integer cpuUnits,
                               @RequestParam(name = "jdbc_ops", required = false) Integer jdbcOps,
                               @RequestParam(name = "redis_ops", required = false) Integer redisOps,
                               @RequestParam(name = "http_calls", required = false) Integer httpCalls,
                               @RequestParam(name = "downstream_delay_ms", required = false) Integer delayMs,
                               @RequestParam(name = "payload_bytes", required = false) Integer payloadBytes) {
        return service.checkout(resolve(cpuUnits, jdbcOps, redisOps, httpCalls, delayMs, payloadBytes));
    }

    @GetMapping("/api/checkout")
    public CheckoutResult get(@RequestParam(name = "cpu_units", required = false) Integer cpuUnits,
                              @RequestParam(name = "jdbc_ops", required = false) Integer jdbcOps,
                              @RequestParam(name = "redis_ops", required = false) Integer redisOps,
                              @RequestParam(name = "http_calls", required = false) Integer httpCalls,
                              @RequestParam(name = "downstream_delay_ms", required = false) Integer delayMs,
                              @RequestParam(name = "payload_bytes", required = false) Integer payloadBytes) {
        return service.checkout(resolve(cpuUnits, jdbcOps, redisOps, httpCalls, delayMs, payloadBytes));
    }

    private Knobs resolve(Integer cpuUnits, Integer jdbcOps, Integer redisOps, Integer httpCalls,
                          Integer delayMs, Integer payloadBytes) {
        Knobs base = service.baseKnobs();
        if (!allowOverrides) {
            return base;
        }
        return base.with(cpuUnits, jdbcOps, redisOps, httpCalls, delayMs, payloadBytes);
    }
}

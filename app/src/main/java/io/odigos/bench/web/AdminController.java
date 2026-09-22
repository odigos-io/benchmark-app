package io.odigos.bench.web;

import io.odigos.bench.config.BenchProperties;
import io.odigos.bench.cpu.CpuWork;
import io.odigos.bench.io.DownstreamOps;
import io.odigos.bench.io.RedisOps;
import io.odigos.bench.io.StateService;
import io.odigos.bench.metrics.BenchMetrics;
import java.lang.management.ManagementFactory;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestMethod;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class AdminController {

    private static final int MAX_CALIBRATE_UNITS = 10_000;
    private static final int MAX_CALIBRATE_ROUNDS = 10_000;
    private static final int MAX_CALIBRATE_THREADS = 64;

    private final StateService state;
    private final RedisOps redis;
    private final DownstreamOps downstream;
    private final BenchMetrics metrics;
    private final BenchProperties props;

    public AdminController(StateService state, RedisOps redis, DownstreamOps downstream,
                           BenchMetrics metrics, BenchProperties props) {
        this.state = state;
        this.redis = redis;
        this.downstream = downstream;
        this.metrics = metrics;
        this.props = props;
    }

    @PostMapping("/admin/reset")
    public ResponseEntity<Map<String, Object>> reset() {
        Map<String, Object> out;
        try {
            out = state.reset();
        } catch (RuntimeException e) {
            out = new LinkedHashMap<>();
            out.put("ok", false);
            out.put("detail", e.toString());
        }
        return ResponseEntity.status(Boolean.TRUE.equals(out.get("ok")) ? HttpStatus.OK
                : HttpStatus.SERVICE_UNAVAILABLE).body(out);
    }

    @RequestMapping(value = "/admin/calibrate", method = {RequestMethod.GET, RequestMethod.POST})
    public ResponseEntity<Map<String, Object>> calibrate(
            @RequestParam(name = "units", defaultValue = "100") int units,
            @RequestParam(name = "rounds", defaultValue = "200") int rounds,
            @RequestParam(name = "threads", defaultValue = "1") int threads) {
        if (units <= 0 || units > MAX_CALIBRATE_UNITS || rounds <= 0 || rounds > MAX_CALIBRATE_ROUNDS
                || threads <= 0 || threads > MAX_CALIBRATE_THREADS) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ok", false);
            out.put("detail", "units in 1.." + MAX_CALIBRATE_UNITS + ", rounds in 1.." + MAX_CALIBRATE_ROUNDS
                    + ", threads in 1.." + MAX_CALIBRATE_THREADS);
            return ResponseEntity.badRequest().body(out);
        }
        int inflight = metrics.inflight();
        if (inflight > 0) {
            Map<String, Object> out = new LinkedHashMap<>();
            out.put("ok", false);
            out.put("inflight", inflight);
            out.put("detail", "calibration refused while checkout requests are in flight");
            return ResponseEntity.status(HttpStatus.CONFLICT).body(out);
        }
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", true);
        out.put("units_per_ms_configured", props.getUnitsPerMs());
        out.putAll(CpuWork.measure(units, rounds, threads));
        return ResponseEntity.ok(out);
    }

    @GetMapping("/admin/config")
    public Map<String, Object> config() {
        Map<String, Object> out = props.describe();
        out.put("available_processors", Runtime.getRuntime().availableProcessors());
        out.put("max_heap_bytes", Runtime.getRuntime().maxMemory());
        out.put("java_version", System.getProperty("java.version"));
        out.put("input_arguments", ManagementFactory.getRuntimeMXBean().getInputArguments());
        return out;
    }

    @GetMapping("/healthz")
    public ResponseEntity<Map<String, Object>> healthz() {
        boolean db = state.databaseReachable();
        boolean redisOk;
        try {
            redisOk = "PONG".equalsIgnoreCase(redis.ping());
        } catch (RuntimeException e) {
            redisOk = false;
        }
        boolean echo = downstream.healthy();
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("ok", db && redisOk && echo);
        out.put("postgres", db);
        out.put("redis", redisOk);
        out.put("downstream", echo);
        return ResponseEntity.status(db && redisOk && echo ? HttpStatus.OK : HttpStatus.SERVICE_UNAVAILABLE)
                .body(out);
    }
}

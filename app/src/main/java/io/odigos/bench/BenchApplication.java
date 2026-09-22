package io.odigos.bench;

import io.odigos.bench.cpu.CpuWork;
import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.Map;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;

@SpringBootApplication
@ConfigurationPropertiesScan
public class BenchApplication {

    public static void main(String[] args) throws IOException {
        if ("calibrate".equalsIgnoreCase(System.getenv("BENCH_MODE"))) {
            calibrateAndExit();
            return;
        }
        SpringApplication.run(BenchApplication.class, args);
    }

    /**
     * Calibration Job entry point: no Spring context, no Postgres, no Redis, no listening port.
     * Measures CpuWork on one thread and on two concurrent threads (HT-sibling inflation) and
     * prints one JSON document to stdout.
     */
    private static void calibrateAndExit() throws IOException {
        int units = envInt("BENCH_CALIBRATE_UNITS", 100);
        int rounds = envInt("BENCH_CALIBRATE_ROUNDS", 200);
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("mode", "calibrate");
        out.put("java_version", System.getProperty("java.version"));
        out.put("available_processors", Runtime.getRuntime().availableProcessors());
        out.put("units", units);
        out.put("rounds", rounds);
        out.put("threads_1", CpuWork.measure(units, rounds, 1));
        out.put("threads_2", CpuWork.measure(units, rounds, 2));
        ObjectMapper mapper = new ObjectMapper();
        mapper.writer().with(SerializationFeature.INDENT_OUTPUT).writeValue(System.out, out);
        System.out.println();
        System.out.flush();
        System.exit(0);
    }

    private static int envInt(String name, int def) {
        String v = System.getenv(name);
        return v == null || v.isBlank() ? def : Integer.parseInt(v.trim());
    }
}

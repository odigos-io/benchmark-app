package io.odigos.bench.io;

import io.odigos.bench.config.BenchProperties;
import java.net.URI;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;

@Component
public class DownstreamOps {

    private final RestTemplate rest;
    private final String base;
    private final URI health;
    private final HttpHeaders headers;

    public DownstreamOps(RestTemplate rest, BenchProperties props) {
        this.rest = rest;
        String url = props.getDownstreamUrl();
        String root = url.endsWith("/") ? url.substring(0, url.length() - 1) : url;
        this.base = root + "/echo?delay_ms=";
        this.health = URI.create(root + "/healthz");
        HttpHeaders h = new HttpHeaders();
        h.setContentType(MediaType.APPLICATION_OCTET_STREAM);
        this.headers = HttpHeaders.readOnlyHttpHeaders(h);
    }

    /** POSTs {@code payload} to the echo service and returns the number of bytes echoed back. */
    public int echo(int delayMs, byte[] payload) {
        ResponseEntity<byte[]> resp = rest.exchange(URI.create(base + delayMs), HttpMethod.POST,
                new HttpEntity<>(payload, headers), byte[].class);
        if (!resp.getStatusCode().is2xxSuccessful()) {
            throw new IllegalStateException("downstream returned " + resp.getStatusCode());
        }
        byte[] body = resp.getBody();
        return body == null ? 0 : body.length;
    }

    public boolean healthy() {
        try {
            return rest.getForEntity(health, String.class).getStatusCode().is2xxSuccessful();
        } catch (RuntimeException e) {
            return false;
        }
    }

    public static byte[] payload(int bytes) {
        byte[] p = new byte[bytes];
        for (int i = 0; i < bytes; i++) {
            p[i] = (byte) ('a' + (i % 26));
        }
        return p;
    }
}

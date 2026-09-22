package io.odigos.bench.config;

import java.net.URI;
import java.time.Duration;
import java.util.Locale;
import org.springframework.boot.web.client.RestTemplateBuilder;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.data.redis.connection.RedisConnectionFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

/**
 * RestTemplate over SimpleClientHttpRequestFactory (HttpURLConnection), the most common
 * blocking client shape, and the one that yields two client spans per outbound call.
 * Connection reuse is controlled by the JVM's keep-alive cache; {@code -Dhttp.maxConnections=64}
 * belongs to the launch flags, not here.
 */
@Configuration
public class ClientsConfig {

    @Bean
    public RestTemplate restTemplate(RestTemplateBuilder builder, BenchProperties props) {
        rejectSelfCall(props.getDownstreamUrl());
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(Duration.ofSeconds(5));
        factory.setReadTimeout(Duration.ofSeconds(30));
        return builder.requestFactory(() -> factory).build();
    }

    @Bean
    public StringRedisTemplate stringRedisTemplate(RedisConnectionFactory connectionFactory) {
        return new StringRedisTemplate(connectionFactory);
    }

    /**
     * A downstream that resolves to this pod turns the HTTP hop into a self-call, which doubles
     * the server work per request and made earlier benchmarks unreadable. Fail at startup.
     */
    static void rejectSelfCall(String downstreamUrl) {
        URI uri = URI.create(downstreamUrl);
        String host = uri.getHost();
        if (host == null) {
            throw new IllegalStateException("DOWNSTREAM_URL has no host: " + downstreamUrl);
        }
        String h = host.toLowerCase(Locale.ROOT);
        String podIp = System.getenv("POD_IP");
        boolean self = h.equals("localhost") || h.equals("127.0.0.1") || h.equals("::1")
                || h.equals("[::1]") || h.equals("0.0.0.0") || h.startsWith("127.")
                || (podIp != null && !podIp.isBlank() && h.equals(podIp.trim().toLowerCase(Locale.ROOT)));
        if (self) {
            throw new IllegalStateException("DOWNSTREAM_URL " + downstreamUrl
                    + " points at this pod; the echo service must run in a separate pod");
        }
    }
}

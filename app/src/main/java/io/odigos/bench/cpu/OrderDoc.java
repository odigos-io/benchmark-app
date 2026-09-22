package io.odigos.bench.cpu;

import java.math.BigDecimal;
import java.util.List;
import java.util.Map;

/**
 * The document one CPU unit serialises, parses and hashes. Shape is fixed (header + 32 lines +
 * two attributes) so the JSON is ~6 KB for every unit; only the values derived from the previous
 * unit's digest change.
 */
public record OrderDoc(
        String orderId,
        String customerId,
        long nonce,
        long createdAtEpochMs,
        String currency,
        String status,
        String channel,
        List<Line> lines,
        Map<String, String> attributes,
        BigDecimal subtotal,
        BigDecimal tax,
        BigDecimal total) {

    public record Line(
            int lineNo,
            String sku,
            String description,
            int quantity,
            BigDecimal unitPrice,
            BigDecimal lineTotal) {
    }
}

package io.odigos.bench.cpu;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Pins the checksum for seed=1, units=10. The same value is documented in app/README.md; if the
 * document shape, the serialiser or the hash chain changes, both must be updated together and
 * the change called out, because results across app versions stop being comparable.
 */
class CpuWorkGoldenTest {

    static final String GOLDEN_SEED_1_UNITS_10 =
            "aa410d70ee23a4ec07fe1808b954ef2cc7d71dfc74cd8bc0a4f107274d9f583b";

    @Test
    void checksumIsPinned() {
        assertEquals(GOLDEN_SEED_1_UNITS_10, CpuWork.run(10, 1L));
    }

    @Test
    void checksumIsDeterministicAcrossRuns() {
        assertEquals(CpuWork.run(25, 7L), CpuWork.run(25, 7L));
        assertNotEquals(CpuWork.run(25, 7L), CpuWork.run(25, 8L));
        assertNotEquals(CpuWork.run(25, 7L), CpuWork.run(26, 7L));
    }

    @Test
    void documentIsAboutSixKilobytes() {
        int bytes = CpuWork.documentBytes(1L);
        assertTrue(bytes >= 5_000 && bytes <= 8_000, "document bytes " + bytes);
    }
}

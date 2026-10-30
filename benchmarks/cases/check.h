// Shared assertions for the hidden benchmark tests.
#ifndef PATCHPILOT_CHECK_H
#define PATCHPILOT_CHECK_H

#include <iostream>

static int failures = 0;

#define CHECK_EQ(actual, expected)                                                    \
    do {                                                                              \
        const auto got = (actual);                                                    \
        const auto want = (expected);                                                 \
        if (!(got == want)) {                                                         \
            std::cout << "FAILED: " #actual " = " << got << ", expected " << want     \
                      << std::endl;                                                   \
            failures++;                                                               \
        }                                                                             \
    } while (0)

static int report() {
    if (failures == 0) {
        std::cout << "All checks passed." << std::endl;
    }
    return failures == 0 ? 0 : 1;
}

#endif

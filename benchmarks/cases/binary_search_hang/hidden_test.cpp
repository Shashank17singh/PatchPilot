#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(firstAtLeast({1, 3, 5, 7}, 5), 2);
    CHECK_EQ(firstAtLeast({1, 3, 5, 7}, 8), 4);
    CHECK_EQ(firstAtLeast({1, 3, 5, 7}, 0), 0);
    return report();
}

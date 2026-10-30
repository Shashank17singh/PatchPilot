#include "check.h"

#define main project_main
#include "main.cpp"
#include "scoring.cpp"
#undef main

int main() {
    CHECK_EQ(clampScore(120, 0, 100), 100);
    CHECK_EQ(clampScore(-5, 0, 100), 0);
    CHECK_EQ(totalScore({120, 50}), 150);
    return report();
}

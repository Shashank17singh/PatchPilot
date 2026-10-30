#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(largestRowSum({{1, 2}, {3, 4}, {1, 1}}), 7);
    CHECK_EQ(largestRowSum({{5}, {1}}), 5);
    return report();
}

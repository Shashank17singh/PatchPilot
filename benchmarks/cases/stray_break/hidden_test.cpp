#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(countEven({2, 4, 6}), 3);
    CHECK_EQ(countEven({1, 3}), 0);
    CHECK_EQ(countEven({1, 2, 3, 4}), 2);
    return report();
}

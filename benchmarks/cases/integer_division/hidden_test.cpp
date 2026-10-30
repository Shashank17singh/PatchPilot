#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(average({1, 2}), 1.5);
    CHECK_EQ(average({2, 4}), 3.0);
    return report();
}

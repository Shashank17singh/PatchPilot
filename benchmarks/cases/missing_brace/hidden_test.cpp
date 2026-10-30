#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(isSorted({1, 2, 3}), true);
    CHECK_EQ(isSorted({3, 1, 2}), false);
    CHECK_EQ(isSorted({1, 3, 2}), false);
    return report();
}

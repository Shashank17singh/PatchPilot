#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(sumRange(1, 5), 15);
    CHECK_EQ(sumRange(3, 3), 3);
    CHECK_EQ(sumRange(-2, 2), 0);
    return report();
}

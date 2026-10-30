#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(static_cast<long long>(factorial(15)), 1307674368000LL);
    CHECK_EQ(static_cast<long long>(factorial(5)), 120LL);
    return report();
}

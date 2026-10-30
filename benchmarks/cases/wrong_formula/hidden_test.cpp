#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    CHECK_EQ(toFahrenheit(100.0), 212.0);
    CHECK_EQ(toFahrenheit(0.0), 32.0);
    return report();
}

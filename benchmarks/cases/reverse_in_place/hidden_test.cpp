#include "check.h"

#define main project_main
#include "main.cpp"
#undef main

int main() {
    std::string odd = "hello";
    reverseInPlace(odd);
    CHECK_EQ(odd, std::string("olleh"));
    std::string even = "ab";
    reverseInPlace(even);
    CHECK_EQ(even, std::string("ba"));
    return report();
}

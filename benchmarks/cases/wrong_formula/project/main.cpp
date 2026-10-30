#include <iostream>

double toFahrenheit(double celsius) {
    return celsius * 9.0 / 5.0 - 32.0;
}

int main() {
    std::cout << "100 C = " << toFahrenheit(100.0) << " F" << std::endl;
    return 0;
}

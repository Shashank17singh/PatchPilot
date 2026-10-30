#include <iostream>

// Returns the sum of the integers from low to high, inclusive.
int sumRange(int low, int high) {
    int total = 0;
    for (int i = low; i < high; i++) {
        total += i;
    }
    return total;
}

int main() {
    std::cout << "sumRange(1, 5) = " << sumRange(1, 5) << std::endl;
    return 0;
}

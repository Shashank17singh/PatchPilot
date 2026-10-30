#include <iostream>
#include <vector>

bool isSorted(const std::vector<int> &values) {
    for (size_t i = 1; i < values.size(); i++) {
        if (values[i - 1] > values[i]) {
            return false;
        }
    return true;
}

int main() {
    std::cout << "isSorted = " << isSorted({1, 2, 3}) << std::endl;
    return 0;
}

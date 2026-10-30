#include <iostream>
#include <vector>

double average(const std::vector<int> &values) {
    int sum = 0;
    for (int value : values) {
        sum += value;
    }
    return sum / static_cast<int>(values.size());
}

int main() {
    std::cout << "average = " << average({1, 2}) << std::endl;
    return 0;
}

#include <iostream>
#include <vector>

int countEven(const std::vector<int> &values) {
    int count = 0;
    for (int value : values) {
        if (value % 2 == 0) {
            count++;
            break;
        }
    }
    return count;
}

int main() {
    std::cout << "countEven = " << countEven({2, 4, 6}) << std::endl;
    return 0;
}

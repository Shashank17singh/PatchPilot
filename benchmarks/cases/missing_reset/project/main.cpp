#include <iostream>
#include <vector>

int largestRowSum(const std::vector<std::vector<int>> &rows) {
    int best = 0;
    int rowSum = 0;
    for (const std::vector<int> &row : rows) {
        for (int value : row) {
            rowSum += value;
        }
        if (rowSum > best) {
            best = rowSum;
        }
    }
    return best;
}

int main() {
    std::cout << "largest = " << largestRowSum({{1, 2}, {3, 4}, {1, 1}}) << std::endl;
    return 0;
}

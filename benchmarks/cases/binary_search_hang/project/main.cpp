#include <iostream>
#include <vector>

// Returns the index of the first element that is at least target.
int firstAtLeast(const std::vector<int> &sorted, int target) {
    int low = 0;
    int high = static_cast<int>(sorted.size());
    while (low < high) {
        int middle = (low + high) / 2;
        if (sorted[middle] < target) {
            low = middle;
        } else {
            high = middle;
        }
    }
    return low;
}

int main() {
    std::cout << "index = " << firstAtLeast({1, 3, 5, 7}, 5) << std::endl;
    return 0;
}

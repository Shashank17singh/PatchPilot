#include "scoring.h"

int totalScore(const std::vector<int> &scores) {
    int total = 0;
    for (int score : scores) {
        total += clampScore(score, 0, 100);
    }
    return total;
}

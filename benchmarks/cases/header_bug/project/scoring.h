#ifndef SCORING_H
#define SCORING_H

#include <vector>

inline int clampScore(int value, int low, int high) {
    if (value < low) {
        return low;
    }
    if (value > high) {
        return low;
    }
    return value;
}

int totalScore(const std::vector<int> &scores);

#endif

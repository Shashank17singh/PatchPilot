// PatchPilot demo project.
//
// A small, correct C++ program for PatchPilot to work on. main() prints each result
// next to the value it should produce, so a regression is obvious at a glance.
//
// To try it: copy this folder, introduce a bug into one of the functions below, then
// run `patchpilot <path-to-your-copy>` and describe the bug in plain English.

#include <cctype>
#include <iostream>
#include <numeric>
#include <string>
#include <vector>

using namespace std;

using ll = long long;

// Returns the sum of all elements, accumulating in 64 bits to avoid overflow.
ll sum_array(const vector<ll> &values) {
    return accumulate(values.begin(), values.end(), static_cast<ll>(0));
}

int add(int a, int b) {
    return a + b;
}

// Returns n! for small non-negative n.
int factorial(int n) {
    int result = 1;
    for (int i = 2; i <= n; i++) {
        result = result * i;
    }
    return result;
}

// Returns true if n is a prime number.
bool isPrime(int n) {
    if (n < 2) {
        return false;
    }
    for (int i = 2; i * i <= n; i++) {
        if (n % i == 0) {
            return false;
        }
    }
    return true;
}

// Returns the largest element. Requires a non-empty vector.
int findMax(const vector<int> &nums) {
    int best = nums[0];
    for (int i = 1; i < (int)nums.size(); i++) {
        if (nums[i] > best) {
            best = nums[i];
        }
    }
    return best;
}

// Returns the arithmetic mean. Requires a non-empty vector.
double average(const vector<int> &nums) {
    int sum = 0;
    for (int x : nums) {
        sum += x;
    }
    return (double)sum / nums.size();
}

// Returns s with its characters in reverse order.
string reverseString(const string &s) {
    string reversed = "";
    for (int i = (int)s.size() - 1; i >= 0; i--) {
        reversed += s[i];
    }
    return reversed;
}

// Counts the vowels (a, e, i, o, u) in s, ignoring case.
int countVowels(const string &s) {
    int count = 0;
    for (char c : s) {
        c = tolower(c);
        if (c == 'a' || c == 'e' || c == 'i' || c == 'o' || c == 'u') {
            count++;
        }
    }
    return count;
}

int main() {
    const vector<int> marks = {72, 95, 60, 88, 45};
    const vector<ll> numbers = {1, 2, 3};

    cout << "sum_array({1, 2, 3})   = " << sum_array(numbers) << "     (expected 6)" << endl;
    cout << "add(2, 3)              = " << add(2, 3) << "     (expected 5)" << endl;
    cout << "factorial(5)           = " << factorial(5) << "   (expected 120)" << endl;
    cout << "isPrime(7)             = " << isPrime(7) << "     (expected 1)" << endl;
    cout << "isPrime(10)            = " << isPrime(10) << "     (expected 0)" << endl;
    cout << "findMax(marks)         = " << findMax(marks) << "    (expected 95)" << endl;
    cout << "average(marks)         = " << average(marks) << "    (expected 72)" << endl;
    cout << "reverseString(hello)   = " << reverseString("hello") << " (expected olleh)" << endl;
    cout << "countVowels(Education) = " << countVowels("Education") << "     (expected 5)" << endl;
    return 0;
}

#include <iostream>
#include <string>
#include <utility>

void reverseInPlace(std::string &text) {
    int length = static_cast<int>(text.size());
    for (int i = 0; i < length; i++) {
        std::swap(text[i], text[length - 1 - i]);
    }
}

int main() {
    std::string text = "hello";
    reverseInPlace(text);
    std::cout << text << std::endl;
    return 0;
}

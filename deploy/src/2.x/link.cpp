#include <fmt/format.h>

#include <rmvl/core.hpp>

int main() {
    rm::Time t{};
    fmt::println("now: {}", t.now());
}

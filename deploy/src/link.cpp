#include <thread>

#include <rmvl/core.hpp>

using namespace std::chrono_literals;

int main() {
    rm::Timer t{};
    t.reset();
    std::this_thread::sleep_for(100ms);
    printf("now: %f\n", t.now());
}
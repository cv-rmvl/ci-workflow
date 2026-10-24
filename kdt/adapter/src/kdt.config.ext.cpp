#include <fmt/format.h>

//! cpp_standard: xxx
constexpr int cpp_standard() {
#ifdef _MSVC_LANG
    constexpr long version = _MSVC_LANG;
#else
    constexpr long version = __cplusplus;
#endif
    static_assert(version >= 201703L, "KDT requires C++17 or later");
    if constexpr (version > 202302L)
        return 26;
    else if constexpr (version > 202002L)
        return 23;
    else if constexpr (version > 201703L)
        return 20;
    else
        return 17;
}

int main() {
    fmt::println("{{\"cpp_standard\": {}}}", cpp_standard());
}

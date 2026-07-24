#pragma once

#include <rmvl/io/serial.hpp>

#include <array>
#include <cstdio>
#include <string_view>

namespace kdt {

inline constexpr std::array baud_rates{
    rm::BaudRate::BR_1200,
    rm::BaudRate::BR_2400,
    rm::BaudRate::BR_4800,
    rm::BaudRate::BR_9600,
    rm::BaudRate::BR_19200,
    rm::BaudRate::BR_38400,
    rm::BaudRate::BR_57600,
    rm::BaudRate::BR_115200,
};

inline constexpr std::array<std::string_view, baud_rates.size()> baud_rate_names{
    "1200",
    "2400",
    "4800",
    "9600",
    "19200",
    "38400",
    "57600",
    "115200",
};

constexpr std::string_view baud_rate_name(rm::BaudRate baud) {
    return baud_rate_names[static_cast<std::size_t>(baud)];
}

inline void enable_line_buffered_stdout() {
    std::setvbuf(stdout, nullptr, _IOLBF, 0);
}

} // namespace kdt

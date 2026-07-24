/**
 * @file io.serial.multiwrite.cpp
 * @author zhaoxi (535394140@qq.com)
 * @brief [KDT Adapter] 持续从文件读取数据并写入串口
 * @version 1.0
 * @date 2026-07-16
 *
 * @copyright Copyright 2026 (c), zhaoxi
 *
 */

#include <fmt/format.h>

#include <csignal>
#include <fstream>
#include <iterator>

#include "io.serial.common.hpp"
#include "kdt.hpp"

namespace {

volatile std::sig_atomic_t stop_requested = 0;

void handle_sigint(int) noexcept { stop_requested = 1; }

} // namespace

int main(int argc, char *argv[]) {
    kdt::enable_line_buffered_stdout();

    kdt::command_line_parser parser(argc, argv, "port baud in");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto port = parser.get("port");
    auto baud = kdt::baud_rates.at(static_cast<std::size_t>(std::stoi(parser.get("baud"))));
    auto in = parser.get("in");

    if (std::signal(SIGINT, handle_sigint) == SIG_ERR) {
        fmt::println(stderr, "Failed to install SIGINT handler");
        return 1;
    }

    rm::SerialPort serial(port, baud);
    if (!serial.isOpened()) {
        fmt::println(stderr, "Failed to open serial port: {}", port);
        return 1;
    }
    fmt::println("Opened serial port {} at {} baud", port, kdt::baud_rate_name(baud));

    while (!stop_requested) {
        std::ifstream ifs(in, std::ios::binary);
        if (!ifs) {
            fmt::println(stderr, "Failed to open input file: {}", in);
            return 1;
        }

        std::string data{std::istreambuf_iterator<char>{ifs}, std::istreambuf_iterator<char>{}};
        if (ifs.bad()) {
            fmt::println(stderr, "Failed to read input file: {}", in);
            return 1;
        }
        ifs.close();

        if (data.empty()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            continue;
        }
        if (stop_requested)
            break;

        fmt::println("Writing {} bytes to serial port", data.size());
        if (!serial.write(data)) {
            if (!stop_requested)
                fmt::println(stderr, "Failed to write data to serial port");
            return stop_requested ? 0 : 1;
        }
        fmt::println("Wrote {} bytes to serial port", data.size());

        std::ofstream clear(in, std::ios::binary | std::ios::trunc);
        if (!clear) {
            fmt::println(stderr, "Failed to clear input file: {}", in);
            return 1;
        }
        clear.close();
        if (!clear) {
            fmt::println(stderr, "Failed to clear input file: {}", in);
            return 1;
        }
    }

    return 0;
}

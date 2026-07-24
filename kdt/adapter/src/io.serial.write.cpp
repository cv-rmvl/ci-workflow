/**
 * @file io.serial.write.cpp
 * @author zhaoxi (535394140@qq.com)
 * @brief [KDT Adapter] 单次写入串口后退出
 * @version 1.0
 * @date 2026-07-15
 *
 * @copyright Copyright 2026 (c), zhaoxi
 *
 */

#include <fmt/format.h>

#include <chrono>

#include "io.serial.common.hpp"
#include "kdt.hpp"

int main(int argc, char *argv[]) {
    kdt::enable_line_buffered_stdout();

    kdt::command_line_parser parser(argc, argv, "port baud data");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto port = parser.get("port");
    auto baud = kdt::baud_rates.at(static_cast<std::size_t>(std::stoi(parser.get("baud"))));
    auto data = parser.get("data");

    return kdt::run_with_timeout(
        [port = std::move(port), baud, data = std::move(data)] {
            rm::SerialPort serial(port, baud);
            if (!serial.isOpened()) {
                fmt::println(stderr, "Failed to open serial port: {}", port);
                return 1;
            }
            fmt::println("Opened serial port {} at {} baud", port, kdt::baud_rate_name(baud));

            fmt::println("Writing {} bytes to serial port", data.size());
            if (!serial.write(data)) {
                fmt::println(stderr, "Failed to write data to serial port");
                return 1;
            }
            fmt::println("Wrote {} bytes to serial port", data.size());
            return 0;
        },
        std::chrono::seconds(30),
        "Serial write");
}

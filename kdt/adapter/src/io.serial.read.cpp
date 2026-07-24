/**
 * @file io.serial.read.cpp
 * @author zhaoxi (535394140@qq.com)
 * @brief [KDT Adapter] 阻塞模式下单次读取串口到文件
 * @version 1.0
 * @date 2026-07-15
 *
 * @copyright Copyright 2026 (c), zhaoxi
 *
 */

#include <fmt/format.h>

#include <chrono>
#include <fstream>

#include "io.serial.common.hpp"
#include "kdt.hpp"

int main(int argc, char *argv[]) {
    kdt::enable_line_buffered_stdout();

    kdt::command_line_parser parser(argc, argv, "port baud out");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto port = parser.get("port");
    auto baud = kdt::baud_rates.at(static_cast<std::size_t>(std::stoi(parser.get("baud"))));
    auto out = parser.get("out");

    return kdt::run_with_timeout(
        [port = std::move(port), baud, out = std::move(out)] {
            rm::SerialPort serial(port, baud);
            if (!serial.isOpened()) {
                fmt::println(stderr, "Failed to open serial port: {}", port);
                return 1;
            }
            fmt::println("Opened serial port {} at {} baud", port, kdt::baud_rate_name(baud));

            std::string res{};
            fmt::println("Reading data from serial port");
            if (!serial.read(res)) {
                fmt::println(stderr, "Failed to read data from serial port");
                return 1;
            }
            fmt::println("Read {} bytes from serial port", res.size());

            std::ofstream ofs(out, std::ios::binary | std::ios::trunc);
            if (!ofs) {
                fmt::println(stderr, "Failed to open output file: {}", out);
                return 1;
            }

            ofs.write(res.data(), static_cast<std::streamsize>(res.size()));
            if (!ofs) {
                fmt::println(stderr, "Failed to write data to output file: {}", out);
                return 1;
            }
            return 0;
        },
        std::chrono::seconds(30), "Serial read");
}

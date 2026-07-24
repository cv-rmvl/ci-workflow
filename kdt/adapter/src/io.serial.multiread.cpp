/**
 * @file io.serial.multiread.cpp
 * @author zhaoxi (535394140@qq.com)
 * @brief [KDT Adapter] 持续从串口读取数据并追加到文件
 * @version 1.0
 * @date 2026-07-16
 *
 * @copyright Copyright 2026 (c), zhaoxi
 *
 */

#include <fmt/format.h>

#include <chrono>
#include <csignal>
#include <fstream>
#include <thread>

#include "io.serial.common.hpp"
#include "kdt.hpp"

namespace {

volatile std::sig_atomic_t stop_requested = 0;

void handle_sigint(int) noexcept { stop_requested = 1; }

} // namespace

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

    if (std::signal(SIGINT, handle_sigint) == SIG_ERR) {
        fmt::println(stderr, "Failed to install SIGINT handler");
        return 1;
    }

    std::ofstream ofs(out, std::ios::binary | std::ios::app);
    if (!ofs) {
        fmt::println(stderr, "Failed to open output file: {}", out);
        return 1;
    }

    rm::SerialPort serial(port, baud, rm::SerialReadMode::NONBLOCK);
    if (!serial.isOpened()) {
        fmt::println(stderr, "Failed to open serial port: {}", port);
        return 1;
    }
    fmt::println("Opened serial port {} at {} baud", port, kdt::baud_rate_name(baud));
    fmt::println("Waiting for data from serial port");

    while (!stop_requested) {
        std::string data;
        if (!serial.read(data) || data.empty()) {
            std::this_thread::sleep_for(std::chrono::milliseconds(10));
            continue;
        }
        fmt::println("Read {} bytes from serial port", data.size());

        ofs.write(data.data(), static_cast<std::streamsize>(data.size()));
        ofs.flush();
        if (!ofs) {
            fmt::println(stderr, "Failed to append data to output file: {}", out);
            return 1;
        }
    }

    return 0;
}

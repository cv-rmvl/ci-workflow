#include <chrono>
#include <fstream>
#include <functional>

#include <fmt/format.h>

#if __cplusplus >= 202002L
#include <rmvl/lpss/node.hpp>
#include <rmvlsrv/std/set_bool.hpp>
#endif

#include "kdt.hpp"

#if __cplusplus >= 202002L
using namespace rm;

async::Task<> call_set_bool(lpss::async::Node &node, lpss::async::Client<srv::SetBool>::ptr client,
                            bool data, std::string output, std::chrono::milliseconds timeout, int &exit_code) {
    srv::SetBool::Request request{};
    request.data = data;

    auto response = co_await client->call(request, timeout);
    if (!response) {
        fmt::println(stderr, "SetBool service call timed out after {} ms", timeout.count());
        exit_code = 5;
        node.shutdown();
        co_return;
    }

    std::ofstream stream(output);
    if (!stream) {
        fmt::println(stderr, "Failed to open response output file: {}", output);
        exit_code = 3;
        node.shutdown();
        co_return;
    }

    stream << fmt::format("success: {}\nmessage: {}\n", response->success ? "true" : "false", response->message);
    if (!stream) {
        fmt::println(stderr, "Failed to write SetBool response: {}", output);
        exit_code = 4;
        node.shutdown();
        co_return;
    }

    fmt::println(
        "SetBool response: success={}, message={} -> {}",
        response->success,
        response->message,
        output);
    node.shutdown();
}
#endif

int main(int argc, char *argv[]) {
    if (argc == 2 && std::string_view(argv[1]) == "--kdt-capability") {
#if __cplusplus >= 202002L
        return 0;
#else
        fmt::println("LPSS service/client requires C++20");
        return kdt::skipped_exit_code;
#endif
    }
    kdt::command_line_parser parser(argc, argv, "node_name service_name data output timeout_ms");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto node_name = parser.get("node_name");
    auto service_name = parser.get("service_name");
    auto data_text = parser.get("data");
    auto output = parser.get("output");
    auto timeout_ms = std::stoi(parser.get("timeout_ms"));
    if (data_text != "0" && data_text != "1") {
        fmt::println(stderr, "SetBool data must be 0 or 1, got: {}", data_text);
        return 1;
    }
    if (timeout_ms <= 0) {
        fmt::println(stderr, "timeout_ms must be greater than zero");
        return 1;
    }

#if __cplusplus >= 202002L
    lpss::async::Node node(node_name);
    auto client = node.createClient<srv::SetBool>(service_name);
    if (!client) {
        fmt::println(stderr, "Failed to create SetBool client: {}", service_name);
        return 2;
    }

    int exit_code = 0;
    bool request_started = false;
    auto discovery_timer = node.createTimer(
        std::chrono::milliseconds(200),
        [&node, client, data = data_text == "1", output, timeout = std::chrono::milliseconds(timeout_ms), &exit_code, &request_started]() {
            if (request_started)
                return;
            request_started = true;
            node.create_task(call_set_bool, std::ref(node), client, data, output, timeout, std::ref(exit_code));
        });
    node.spin();
    return exit_code;
#else
    fmt::println("SKIP: LPSS service/client requires C++20 (node: {}, service: {})", node_name, service_name);
    return kdt::skipped_exit_code;
#endif
}

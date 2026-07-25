#include <chrono>
#include <fstream>
#include <functional>

#include <fmt/format.h>

#if __cplusplus >= 202002L
#include <rmvl/lpss/node.hpp>
#include <rmvlsrv/test/test_types.hpp>
#endif

#include "kdt.hpp"
#if __cplusplus >= 202002L
#include "kdt_test_types.hpp"

using namespace rm;

async::Task<> call_test_types(lpss::async::Node &node, lpss::async::Client<srv::TestTypes>::ptr client,
                              std::string output, std::chrono::milliseconds timeout, int &exit_code) {
    srv::TestTypes::Request request{};
    request.data = kdt::make_test_types();

    const auto request_data = request.serialize();
    const auto decoded_request = srv::TestTypes::Request::deserialize(request_data.data());
    if (request_data.size() != request.compact_size() ||
        !kdt::valid_test_types(decoded_request.data)) {
        fmt::println(stderr, "TestTypes request serialization round trip failed");
        exit_code = 2;
        node.shutdown();
        co_return;
    }

    const auto deadline = std::chrono::steady_clock::now() + timeout;
    if (!(co_await client->wait(timeout))) {
        fmt::println(stderr, "TestTypes service did not come online after {} ms", timeout.count());
        exit_code = 3;
        node.shutdown();
        co_return;
    }

    const auto now = std::chrono::steady_clock::now();
    if (now >= deadline) {
        fmt::println(stderr, "TestTypes service call timed out after {} ms", timeout.count());
        exit_code = 3;
        node.shutdown();
        co_return;
    }

    auto response = co_await client->call(request, deadline - now);
    if (!response) {
        fmt::println(stderr, "TestTypes service call timed out after {} ms", timeout.count());
        exit_code = 3;
        node.shutdown();
        co_return;
    }

    const auto response_data = response->serialize();
    const auto decoded_response = srv::TestTypes::Response::deserialize(response_data.data());
    const auto valid = response_data.size() == response->compact_size() && decoded_response.success && kdt::valid_test_types(decoded_response.data);

    std::ofstream stream(output);
    if (!stream) {
        fmt::println(stderr, "Failed to open validation output file: {}", output);
        exit_code = 4;
        node.shutdown();
        co_return;
    }
    stream << (valid ? "pass" : "fail") << std::endl;
    fmt::println("TestTypes service response {} -> {}", valid ? "passed" : "failed", output);
    exit_code = valid ? 0 : 5;
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
    kdt::command_line_parser parser(
        argc, argv, "node_name service_name output timeout_ms");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    const auto node_name = parser.get("node_name");
    const auto service_name = parser.get("service_name");
    const auto timeout_ms = std::stoi(parser.get("timeout_ms"));
    if (timeout_ms <= 0) {
        fmt::println(stderr, "timeout_ms must be greater than zero");
        return 1;
    }

#if __cplusplus >= 202002L
    lpss::async::Node node(node_name);
    auto client = node.createClient<srv::TestTypes>(service_name);
    if (!client) {
        fmt::println(stderr, "Failed to create TestTypes client");
        return 2;
    }

    int exit_code = 0;
    node.create_task(
        call_test_types,
        std::ref(node),
        client,
        parser.get("output"),
        std::chrono::milliseconds(timeout_ms),
        std::ref(exit_code));
    node.spin();
    return exit_code;
#else
    fmt::println("SKIP: LPSS service/client requires C++20 (node: {}, service: {})", node_name, service_name);
    return kdt::skipped_exit_code;
#endif
}

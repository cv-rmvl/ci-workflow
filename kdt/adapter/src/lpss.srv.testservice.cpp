#include <fmt/format.h>

#if __cplusplus >= 202002L
#include <rmvl/lpss/node.hpp>
#include <rmvlsrv/test/test_types.hpp>
#endif

#include "kdt.hpp"
#if __cplusplus >= 202002L
#include "kdt_test_types.hpp"

using namespace rm;
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
    kdt::command_line_parser parser(argc, argv, "node_name service_name");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    const auto node_name = parser.get("node_name");
    const auto service_name = parser.get("service_name");

#if __cplusplus >= 202002L
    lpss::async::Node node(node_name);
    auto service = node.createService<srv::TestTypes>(
        service_name,
        [](const srv::TestTypes::Request &request,
           srv::TestTypes::Response &response) {
            response.success = kdt::valid_test_types(request.data);
            response.data = request.data;
            fmt::println(
                "TestTypes request {}", response.success ? "passed" : "failed");
        });
    if (!service) {
        fmt::println(stderr, "Failed to create TestTypes service: {}", service_name);
        return 2;
    }

    fmt::println("TestTypes service ready: {}", service_name);
    node.spin();
#else
    fmt::println("SKIP: LPSS service/client requires C++20 (node: {}, service: {})", node_name, service_name);
    return kdt::skipped_exit_code;
#endif
}

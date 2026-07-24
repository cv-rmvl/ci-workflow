#include <fmt/format.h>

#if __cplusplus >= 202002L
#include <rmvl/lpss/node.hpp>
#include <rmvlsrv/std/set_bool.hpp>
#endif

#include "kdt.hpp"

#if __cplusplus >= 202002L
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

    auto node_name = parser.get("node_name");
    auto service_name = parser.get("service_name");

#if __cplusplus >= 202002L
    lpss::async::Node node(node_name);
    auto service = node.createService<srv::SetBool>(
        service_name, [](const srv::SetBool::Request &request, srv::SetBool::Response &response) {
            response.success = true;
            response.message = request.data ? "enabled" : "disabled";
            fmt::println("SetBool request: data={} -> success=true, message={}", request.data, response.message);
        });
    if (!service) {
        fmt::println(stderr, "Failed to create SetBool service: {}", service_name);
        return 2;
    }

    fmt::println("SetBool service ready: {}", service_name);
    node.spin();
#else
    fmt::println("SKIP: LPSS service/client requires C++20 (node: {}, service: {})", node_name, service_name);
    return kdt::skipped_exit_code;
#endif
}

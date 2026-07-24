#include <fstream>

#include <fmt/format.h>

#include <rmvl/lpss/node.hpp>

#include "kdt.hpp"
#include "kdt_test_types.hpp"

using namespace rm;

#if __cplusplus >= 202002L

class KdtTestTypesSubscriber : public lpss::async::Node {
public:
    KdtTestTypesSubscriber(std::string_view node_name, std::string_view topic, std::string_view output)
        : Node(node_name), _stream(output.data(), std::ios::out) {
        if (!_stream)
            return;
        _subscriber = createSubscriber<msg::TestTypes>(topic, [this, output = std::string(output)](const msg::TestTypes &data) {
            const auto valid = kdt::valid_test_types(data);
            _stream << (valid ? "pass" : "fail") << std::endl;
            fmt::println("TestTypes message {} -> {}", valid ? "passed" : "failed", output);
        });
    }

    bool valid() const noexcept { return _stream && _subscriber; }

private:
    lpss::async::Subscriber<msg::TestTypes>::ptr _subscriber{};
    std::ofstream _stream{};
};

#endif

int main(int argc, char *argv[]) {
    kdt::command_line_parser parser(argc, argv, "node_name topic output");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

#if __cplusplus >= 202002L
    KdtTestTypesSubscriber node(parser.get("node_name"), parser.get("topic"), parser.get("output"));
    if (!node.valid()) {
        fmt::println(stderr, "Failed to create TestTypes subscriber or output file");
        return 2;
    }
    node.spin();
#else
    const auto output = parser.get("output");
    std::ofstream stream(output, std::ios::out);
    if (!stream) {
        fmt::println(stderr, "Failed to open TestTypes subscriber output file: {}", output);
        return 2;
    }
    lpss::Node node(parser.get("node_name"));
    auto subscriber = node.createSubscriber<msg::TestTypes>(
        parser.get("topic"), [&stream, output](const msg::TestTypes &data) {
            const auto valid = kdt::valid_test_types(data);
            stream << (valid ? "pass" : "fail") << std::endl;
            fmt::println("TestTypes message {} -> {}", valid ? "passed" : "failed", output);
        });
    if (subscriber.invalid()) {
        fmt::println(stderr, "Failed to create TestTypes subscriber: {}", parser.get("topic"));
        return 2;
    }
    kdt::wait_for_interrupt();
#endif
}

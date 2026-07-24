#include <fmt/format.h>

#include <rmvl/lpss/node.hpp>

#include "kdt.hpp"
#include "kdt_test_types.hpp"

using namespace rm;

#if __cplusplus >= 202002L

class KdtTestTypesPublisher : public lpss::async::Node {
public:
    KdtTestTypesPublisher(std::string_view node_name, std::string_view topic, msg::TestTypes data, int32_t period_ms) : Node(node_name) {
        _publisher = createPublisher<msg::TestTypes>(topic);
        _timer = createTimer(std::chrono::milliseconds(period_ms), [this, data = std::move(data)]() {
            _publisher->publish(data);
        });
    }

private:
    lpss::async::Publisher<msg::TestTypes>::ptr _publisher{};
    lpss::async::Timer::ptr _timer{};
};

#endif

int main(int argc, char *argv[]) {
    kdt::command_line_parser parser(argc, argv, "node_name topic period_ms");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto data = kdt::make_test_types();
    if (!kdt::valid_test_types_round_trip(data)) {
        fmt::println(stderr, "TestTypes direct serialization round trip failed");
        return 2;
    }

    const auto period_ms = std::stoi(parser.get("period_ms"));
    if (period_ms <= 0) {
        fmt::println(stderr, "period_ms must be greater than zero");
        return 1;
    }

#if __cplusplus >= 202002L
    KdtTestTypesPublisher node(parser.get("node_name"), parser.get("topic"), std::move(data), period_ms);
    fmt::println("TestTypes publisher ready: {}", parser.get("topic"));
    node.spin();
#else
    kdt::install_interrupt_handler();
    lpss::Node node(parser.get("node_name"));
    auto publisher = node.createPublisher<msg::TestTypes>(parser.get("topic"));
    if (publisher.invalid()) {
        fmt::println(stderr, "Failed to create TestTypes publisher: {}", parser.get("topic"));
        return 2;
    }
    fmt::println("TestTypes publisher ready: {}", parser.get("topic"));
    while (kdt::keep_running()) {
        publisher.publish(data);
        std::this_thread::sleep_for(std::chrono::milliseconds(period_ms));
    }
#endif
}

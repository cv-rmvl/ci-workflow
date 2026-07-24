#include <fmt/format.h>

#include <rmvl/lpss/node.hpp>
#include <rmvlmsg/std/string.hpp>

#include "kdt.hpp"

using namespace rm;

#if __cplusplus >= 202002L

class KdtStringPublisher : public lpss::async::Node {
public:
    KdtStringPublisher(std::string_view node_name, std::string_view topic, std::string_view data, int32_t period_ms) : Node(node_name) {
        _pub = this->createPublisher<msg::String>(topic);
        _timer = this->createTimer(std::chrono::milliseconds(period_ms), [this, data]() {
            msg::String msg{};
            msg.data = data;
            _pub->publish(msg);
        });
    }

private:
    lpss::async::Publisher<msg::String>::ptr _pub{};
    lpss::async::Timer::ptr _timer{};
};

#endif

int main(int argc, char *argv[]) {
    kdt::command_line_parser parser(argc, argv, "node_name topic message period_ms");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto node_name = parser.get("node_name");
    auto topic = parser.get("topic");
    auto message = parser.get("message");
    auto period_ms = std::stoi(parser.get("period_ms"));

#if __cplusplus >= 202002L
    KdtStringPublisher kcsp(node_name, topic, message, period_ms);
    kcsp.spin();
#else
    kdt::install_interrupt_handler();
    lpss::Node nd(node_name);
    auto pub = nd.createPublisher<msg::String>(topic);
    if (pub.invalid()) {
        fmt::println(stderr, "Failed to create String publisher: {}", topic);
        return 2;
    }
    while (kdt::keep_running()) {
        msg::String msg{};
        msg.data = message;
        pub.publish(msg);
        std::this_thread::sleep_for(std::chrono::milliseconds(period_ms));
    }
#endif
}

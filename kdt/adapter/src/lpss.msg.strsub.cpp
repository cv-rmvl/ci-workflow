#include <cstdio>
#include <fstream>

#include <fmt/format.h>

#include <rmvl/lpss/node.hpp>
#include <rmvlmsg/std/string.hpp>

#include "kdt.hpp"

using namespace rm;

#if __cplusplus >= 202002L

class KdtCstStringSubscriber : public lpss::async::Node {
public:
    KdtCstStringSubscriber(std::string_view node_name, std::string_view topic, std::string_view outfile) : Node(node_name) {
        _ofs = std::ofstream(outfile.data(), std::ios::out);
        _sub = this->createSubscriber<msg::String>(topic, [this, outfile](const msg::String &msg) {
            _ofs << msg.data << std::endl;
            fmt::println("Received message: {} -> {}", msg.data, outfile);
            fmt::println("KDT_RESULT: {}", msg.data);
            std::fflush(stdout);
        });
    }

private:
    lpss::async::Subscriber<msg::String>::ptr _sub{};
    std::ofstream _ofs{};
};

#endif

int main(int argc, char *argv[]) {
    kdt::command_line_parser parser(argc, argv, "node_name topic outfile");
    if (!parser.parse()) {
        fmt::println(stderr, "{}", parser.usage());
        return 1;
    }

    auto node_name = parser.get("node_name");
    auto topic = parser.get("topic");
    auto outfile = parser.get("outfile");

#if __cplusplus >= 202002L
    KdtCstStringSubscriber node(node_name, topic, outfile);
    node.spin();
#else
    std::ofstream stream(outfile, std::ios::out);
    if (!stream) {
        fmt::println(stderr, "Failed to open subscriber output file: {}", outfile);
        return 2;
    }
    lpss::Node node(node_name);
    auto subscriber = node.createSubscriber<msg::String>(
        topic, [&stream, outfile](const msg::String &msg) {
            stream << msg.data << std::endl;
            fmt::println("Received message: {} -> {}", msg.data, outfile);
            fmt::println("KDT_RESULT: {}", msg.data);
            std::fflush(stdout);
        });
    if (subscriber.invalid()) {
        fmt::println(stderr, "Failed to create String subscriber: {}", topic);
        return 2;
    }
    kdt::wait_for_interrupt();
#endif
}

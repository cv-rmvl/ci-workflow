#include <cstdio>
#include <exception>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

#include <fmt/format.h>

#include "kdt.hpp"

#if __cplusplus >= 202002L
#include <charconv>
#include <chrono>
#include <cstdint>
#include <map>
#include <set>
#include <system_error>
#include <utility>

#include <rmvl/lpss/node.hpp>
#endif

namespace {

void help() {
    fmt::print(
        "用法: lpss.tool <node|topic|service> <command> [参数]\n"
        "  node help | list [-c] | info <node>\n"
        "  topic help | list [-c] | info <topic>\n"
        "  service help | list [-c] | info <service>\n"
        "选项:\n"
        "  --domain N   查询域 ID，默认 0，范围 0 至 255\n"
        "  --wait-ms N  初始发现窗口，默认 1000 ms，范围 1 至 86400000 ms\n"
        "  -c           list 仅输出去重后的数量（node 按 GUID，其余按名称）\n"
        "  --           后续参数全部视为位置参数\n"
        "查询结果为窗口结束时的快照，不保证已发现网络中全部节点\n"
        "服务传输话题通过 service 命令访问\n"
        "服务名查询兼容有无一个前导斜杠，列表显示请求话题 lq/ 后的名称\n"
        "退出码: 0 成功，1 参数错误，2 运行错误，77 不支持，130 中断\n"
        "能力探测: lpss.tool --kdt-capability\n");
}

bool wants_help(int argc, char **argv) {
    if (argc == 1)
        return true;
    if (argc == 2) {
        const std::string_view arg(argv[1]);
        if (arg == "help" || arg == "--help" || arg == "-h")
            return true;
        return arg == "node" || arg == "topic" || arg == "service";
    }
    if (argc == 3) {
        const std::string_view group(argv[1]), arg(argv[2]);
        return (group == "node" || group == "topic" || group == "service") &&
               (arg == "help" || arg == "--help" || arg == "-h");
    }
    return false;
}

#if __cplusplus >= 202002L

using Milliseconds = std::chrono::milliseconds;
template <class T = void>
using Task = rm::async::Task<T>;

class ArgumentError : public std::runtime_error {
public:
    using std::runtime_error::runtime_error;
};

struct Options {
    std::string group, command;
    std::vector<std::string> args;
    bool count_only{};
    std::uint8_t domain{};
    Milliseconds wait{1000};
};

std::uint64_t decimal(std::string_view text) {
    std::uint64_t value{};
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (text.empty() || result.ec != std::errc{} || result.ptr != text.data() + text.size())
        throw ArgumentError("选项值必须为十进制非负整数");
    return value;
}

Options parse(int argc, char **argv) {
    if (argc < 3)
        throw ArgumentError("缺少命令");
    Options o;
    o.group = argv[1];
    o.command = argv[2];
    const bool node = o.group == "node";
    const bool topic = o.group == "topic";
    const bool service = o.group == "service";
    const bool list = o.command == "list";
    if (!(node || topic || service) || !(list || o.command == "info"))
        throw ArgumentError("未知命令，使用 lpss.tool help 查看帮助");

    std::set<std::string> seen;
    bool positional_only = false;
    for (int i = 3; i < argc; ++i) {
        const std::string arg(argv[i]);
        if (!positional_only && arg == "--") {
            positional_only = true;
            continue;
        }
        if (positional_only || arg.empty() || arg.front() != '-') {
            o.args.push_back(arg);
            continue;
        }
        if (!seen.insert(arg).second)
            throw ArgumentError("重复选项: " + arg);
        if (arg == "-c" && list) {
            o.count_only = true;
            continue;
        }
        if (arg != "--wait-ms" && arg != "--domain")
            throw ArgumentError("当前命令不支持选项: " + arg);
        if (++i >= argc)
            throw ArgumentError("选项缺少值: " + arg);
        const auto value = decimal(argv[i]);
        if (arg == "--domain") {
            if (value > 255)
                throw ArgumentError("域 ID 超出范围，必须为 0 至 255");
            o.domain = static_cast<std::uint8_t>(value);
            continue;
        }
        if (value == 0 || value > 86400000)
            throw ArgumentError("时间参数超出范围: " + arg);
        o.wait = Milliseconds(static_cast<std::int64_t>(value));
    }
    const std::size_t expected = list ? 0 : 1;
    if (o.args.size() != expected)
        throw ArgumentError("位置参数数量不正确，使用 lpss.tool help 查看帮助");
    if (!list) {
        const auto &name = o.args.front();
        if (name.empty())
            throw ArgumentError("位置参数不能为空");
        if (service) {
            // 与 RMVL 创建服务时的映射和长度校验保持一致。
            if (rm::lpss::srv2topic::request(name).size() > 63 ||
                rm::lpss::srv2topic::response(name).size() > 63)
                throw ArgumentError("服务传输话题名称超出协议长度限制");
        } else if (name.size() > (node ? 255u : 63u)) {
            throw ArgumentError("名称超出协议长度限制");
        }
        if (topic && (name.starts_with("lq/") || name.starts_with("lr/")))
            throw ArgumentError("服务传输话题请通过 service 命令访问");
    }
    return o;
}

using NamedTypes = std::map<std::string, std::string>;

struct ServiceInfo {
    std::string request, response;

    std::string type() const {
        constexpr std::string_view req_suffix = "_Request", res_suffix = "_Response";
        if (request.ends_with(req_suffix) && response.ends_with(res_suffix)) {
            const auto req = request.substr(0, request.size() - req_suffix.size());
            const auto res = response.substr(0, response.size() - res_suffix.size());
            if (req == res)
                return req;
        }
        return request + " -> " + response;
    }
};

struct NodeInfo {
    rm::lpss::Guid guid;
    std::string name;
    NamedTypes pubs, subs;
    std::map<std::string, ServiceInfo> servers, clients;
};

bool transport(std::string_view name) {
    return name.starts_with("lq/") || name.starts_with("lr/");
}

void print_names(const std::set<std::string> &names, bool count_only) {
    if (count_only)
        fmt::println("{}", names.size());
    else
        for (const auto &name : names)
            fmt::println("{}", name);
}

template <class Map>
void print_section(std::string_view title, const Map &items) {
    fmt::println("{}:", title);
    if (items.empty()) {
        fmt::println("  (none)");
        return;
    }
    for (const auto &[name, value] : items) {
        (void)value;
        fmt::println("  {}", name);
    }
}

std::string unique_type(const std::set<std::string> &types, const std::string &name) {
    if (types.empty())
        throw std::runtime_error("未发现服务: " + name);
    if (types.size() != 1)
        throw std::runtime_error("服务存在多个类型: " + name);
    return *types.begin();
}

class Tool final : public rm::lpss::async::Node {
public:
    explicit Tool(Options options) : Node("kdt_lpss_tool", options.domain), _options(std::move(options)) {}
    int result{130};

    Task<> run() {
        try {
            rm::async::Timer timer(_ctx);
            co_await timer.sleep_for(_options.wait);
            result = execute();
        } catch (const std::exception &e) {
            fmt::println(stderr, "运行错误: {}", e.what());
            result = 2;
        } catch (...) {
            fmt::println(stderr, "运行错误: 未知异常");
            result = 2;
        }
        std::fflush(stdout);
        std::fflush(stderr);
        shutdown();
        co_return;
    }

private:
    Options _options;

    // 只能在本节点事件循环中调用，不与其他线程并发访问发现表。
    std::vector<NodeInfo> graph() const {
        std::vector<NodeInfo> nodes;
        for (const auto &[guid, storage] : _discovered_nodes)
            nodes.push_back({guid, storage.rndp_msg.name, {}, {}, {}, {}});
        const auto owner = [&](rm::lpss::Guid endpoint) -> NodeInfo * {
            for (auto &node : nodes)
                if (node.guid.host() == endpoint.host() && node.guid.pid() == endpoint.pid())
                    return &node;
            return nullptr;
        };
        for (const auto &[name, storage] : _discovered_writers)
            for (const auto &guid : storage.writers)
                if (auto *node = owner(guid))
                    node->pubs[name] = storage.msgtype;
        for (const auto &[name, storage] : _discovered_readers)
            for (const auto &[guid, locator] : storage.readers) {
                (void)locator;
                if (auto *node = owner(guid))
                    node->subs[name] = storage.msgtype;
            }
        for (auto &node : nodes) {
            // 服务端订阅请求并发布响应，客户端采用相反方向。
            // 查询键为请求传输话题 lq/ 后的原始后缀。
            for (const auto &[name, type] : node.subs) {
                if (!name.starts_with("lq/"))
                    continue;
                const auto response = node.pubs.find("lr/" + name.substr(3));
                if (response != node.pubs.end())
                    node.servers[name.substr(3)] = {type, response->second};
            }
            for (const auto &[name, type] : node.pubs) {
                if (!name.starts_with("lq/"))
                    continue;
                const auto response = node.subs.find("lr/" + name.substr(3));
                if (response != node.subs.end())
                    node.clients[name.substr(3)] = {type, response->second};
            }
            for (auto it = node.pubs.begin(); it != node.pubs.end();) {
                if (transport(it->first))
                    it = node.pubs.erase(it);
                else
                    ++it;
            }
            for (auto it = node.subs.begin(); it != node.subs.end();) {
                if (transport(it->first))
                    it = node.subs.erase(it);
                else
                    ++it;
            }
        }
        return nodes;
    }

    std::set<std::string> topics() const {
        std::set<std::string> names;
        for (const auto &[name, storage] : _discovered_writers) {
            (void)storage;
            if (!transport(name))
                names.insert(name);
        }
        for (const auto &[name, storage] : _discovered_readers) {
            (void)storage;
            if (!transport(name))
                names.insert(name);
        }
        return names;
    }

    std::string topic_type(const std::string &name) const {
        const auto writer = _discovered_writers.find(name);
        const auto reader = _discovered_readers.find(name);
        if (writer == _discovered_writers.end() && reader == _discovered_readers.end())
            throw std::runtime_error("未发现话题: " + name);
        if (writer != _discovered_writers.end() && reader != _discovered_readers.end() &&
            writer->second.msgtype != reader->second.msgtype)
            throw std::runtime_error("话题发布与订阅类型不一致: " + name);
        return writer != _discovered_writers.end() ? writer->second.msgtype : reader->second.msgtype;
    }

    int execute() const {
        const auto &group = _options.group;
        const bool list = _options.command == "list";
        const auto target = list ? std::string{} : _options.args.front();
        const auto nodes = graph();
        if (group == "node") {
            if (list && _options.count_only) {
                // 发现表以 GUID 为键，同名不同 GUID 的节点分别计数。
                fmt::println("{}", nodes.size());
            } else if (list) {
                std::set<std::string> names;
                for (const auto &node : nodes)
                    names.insert(node.name);
                print_names(names, _options.count_only);
            } else {
                bool found = false;
                for (const auto &node : nodes) {
                    if (node.name != target)
                        continue;
                    found = true;
                    fmt::println("Node: {}\nGUID: {:016x}", node.name, node.guid.full);
                    print_section("Publish Topics", node.pubs);
                    print_section("Subscribe Topics", node.subs);
                    print_section("Server Services", node.servers);
                    print_section("Client Services", node.clients);
                }
                if (!found)
                    throw std::runtime_error("未发现节点: " + target);
            }
            return 0;
        }
        if (group == "topic") {
            if (list) {
                print_names(topics(), _options.count_only);
                return 0;
            }
            fmt::println("Type: {}", topic_type(target));
            NamedTypes publishers, subscribers;
            for (const auto &node : nodes) {
                if (node.pubs.count(target))
                    publishers[node.name] = {};
                if (node.subs.count(target))
                    subscribers[node.name] = {};
            }
            print_section("Publisher Node", publishers);
            print_section("Subscriber Node", subscribers);
            return 0;
        }
        // 从实际请求传输话题提取键，严格复用 RMVL 的单个前导斜杠映射。
        const auto service_key = list ? std::string{} : rm::lpss::srv2topic::request(target).substr(3);
        std::set<std::string> names, types;
        NamedTypes servers, clients;
        for (const auto &node : nodes) {
            const auto collect = [&](const auto &services, auto &owners) {
                for (const auto &[name, info] : services) {
                    if (list)
                        names.insert(name);
                    else if (name == service_key) {
                        types.insert(info.type());
                        owners[node.name] = {};
                    }
                }
            };
            collect(node.servers, servers);
            collect(node.clients, clients);
        }
        if (list) {
            print_names(names, _options.count_only);
            return 0;
        }
        fmt::println("Type: {}", unique_type(types, target));
        print_section("Server Node", servers);
        print_section("Client Node", clients);
        return 0;
    }
};

#endif

} // namespace

int main(int argc, char **argv) {
    if (wants_help(argc, argv)) {
        help();
        return 0;
    }
    if (argc == 2 && std::string_view(argv[1]) == "--kdt-capability") {
#if __cplusplus >= 202002L
        return 0;
#else
        fmt::println("LPSS tool requires C++20");
        return kdt::skipped_exit_code;
#endif
    }
#if __cplusplus >= 202002L
    try {
        auto options = parse(argc, argv);
        Tool tool(std::move(options));
        tool.create_task(&Tool::run, &tool);
        tool.spin();
        std::fflush(stdout);
        std::fflush(stderr);
        return tool.result;
    } catch (const ArgumentError &e) {
        fmt::println(stderr, "参数错误: {}", e.what());
        return 1;
    } catch (const std::exception &e) {
        fmt::println(stderr, "运行错误: {}", e.what());
        return 2;
    } catch (...) {
        fmt::println(stderr, "运行错误: 未知异常");
        return 2;
    }
#else
    fmt::println(stderr, "SKIP: LPSS tool requires C++20");
    return kdt::skipped_exit_code;
#endif
}

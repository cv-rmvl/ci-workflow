#include <algorithm>
#include <csignal>
#include <filesystem>
#include <fstream>
#include <future>
#include <mutex>
#include <optional>

#ifndef _WIN32
#include <sys/wait.h>
#else
#include <windows.h>
#endif

#include <fmt/format.h>
#include <rmvl/io/netapp.hpp>

#include "pathconf.hpp"

namespace fs = std::filesystem;

using namespace std::chrono_literals;

using rm::json;
using rm::Request;
using rm::Response;

namespace kdt {

struct Config {
    std::uint16_t port{8765};
    bool with_ai{};
};

struct ExecutionJob {
    std::mutex mutex;
    std::string output;
    std::future<void> task;
    bool running{};
    bool finished{};
    bool ok{};
    int exit_code{};
    std::uint64_t revision{};
};

std::string read_text(const fs::path &path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream)
        throw std::runtime_error(fmt::format("Cannot read file: {}", path.string()));
    return {std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>()};
}

void write_text(const fs::path &path, std::string_view content) {
    if (content.find_first_not_of(" \t\r\n") == std::string_view::npos)
        throw std::invalid_argument("Refusing to save an empty YAML document");

    fs::create_directories(path.parent_path());
    auto temporary = path;
    temporary += ".tmp";
    std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
    if (!stream)
        throw std::runtime_error(fmt::format("Cannot write temporary file: {}", temporary.string()));
    stream.write(content.data(), static_cast<std::streamsize>(content.size()));
    stream.close();
    if (!stream) {
        fs::remove(temporary);
        throw std::runtime_error(fmt::format("Cannot write temporary file: {}", temporary.string()));
    }

#ifdef _WIN32
    if (!MoveFileExW(temporary.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH)) {
        const auto error = GetLastError();
        fs::remove(temporary);
        throw std::runtime_error(
            fmt::format("Cannot replace file: {} (Windows error {})",
                        path.string(), error));
    }
#else
    std::error_code error;
    fs::rename(temporary, path, error);
    if (error) {
        fs::remove(temporary);
        throw std::runtime_error(
            fmt::format("Cannot replace file: {} ({})", path.string(),
                        error.message()));
    }
#endif
}

void prune_empty_parents(fs::path path, const fs::path &root) {
    path = path.parent_path();
    while (path != root && path.string().size() > root.string().size() &&
           fs::is_empty(path)) {
        fs::remove(path);
        path = path.parent_path();
    }
}

bool is_yaml(const fs::path &path) {
    auto ext = path.extension().string();
    return ext == ".yml" || ext == ".yaml";
}

fs::path path_from_utf8(std::string_view value) {
#ifdef __cpp_char8_t
    const auto *begin = reinterpret_cast<const char8_t *>(value.data());
    return fs::path(begin, begin + value.size());
#else
    return fs::u8path(value.begin(), value.end());
#endif
}

bool valid_segment(std::string_view value) {
    static const std::regex pattern(R"(^[A-Za-z0-9_-]+$)");
    return std::regex_match(value.begin(), value.end(), pattern);
}

bool valid_python_identifier(std::string_view value) {
    static const std::regex pattern(R"(^[A-Za-z_][A-Za-z0-9_]*$)");
    return std::regex_match(value.begin(), value.end(), pattern);
}

struct ComponentTarget {
    fs::path file;
    std::string function;
};

ComponentTarget component_target(const fs::path &components_root,
                                 std::string_view component) {
    std::vector<std::string> segments;
    std::size_t begin{};
    while (begin < component.size()) {
        const auto end = component.find('.', begin);
        auto segment = component.substr(
            begin, end == std::string_view::npos ? component.size() - begin
                                                 : end - begin);
        if (!valid_python_identifier(segment))
            throw std::invalid_argument("Invalid Python component");
        segments.emplace_back(segment);
        if (end == std::string_view::npos)
            break;
        begin = end + 1;
    }
    if (segments.size() < 2 || component.back() == '.')
        throw std::invalid_argument("Invalid Python component");

    auto file = components_root;
    for (std::size_t i = 0; i + 2 < segments.size(); ++i)
        file /= segments[i];
    file /= segments[segments.size() - 2] + ".py";
    return {std::move(file), std::move(segments.back())};
}

std::string_view source_line(std::string_view source, std::size_t begin,
                             std::size_t end) {
    if (end > begin && source[end - 1] == '\n')
        --end;
    if (end > begin && source[end - 1] == '\r')
        --end;
    return source.substr(begin, end - begin);
}

bool is_function_header(std::string_view line, std::string_view function) {
    constexpr std::string_view async_prefix = "async def ";
    constexpr std::string_view def_prefix = "def ";
    if (line.starts_with(async_prefix))
        line.remove_prefix(async_prefix.size());
    else if (line.starts_with(def_prefix))
        line.remove_prefix(def_prefix.size());
    else
        return false;

    if (!line.starts_with(function))
        return false;
    line.remove_prefix(function.size());
    while (!line.empty() && (line.front() == ' ' || line.front() == '\t'))
        line.remove_prefix(1);
    return !line.empty() && line.front() == '(';
}

std::optional<std::pair<std::size_t, std::size_t>>
top_level_function_range(std::string_view source, std::string_view function) {
    std::vector<std::size_t> starts{0};
    for (std::size_t i = 0; i < source.size(); ++i)
        if (source[i] == '\n' && i + 1 < source.size())
            starts.push_back(i + 1);

    for (std::size_t index = 0; index < starts.size(); ++index) {
        const auto begin = starts[index];
        const auto end = index + 1 < starts.size() ? starts[index + 1] : source.size();
        if (!is_function_header(source_line(source, begin, end), function))
            continue;

        auto block_begin = begin;
        auto decorator_index = index;
        while (decorator_index > 0) {
            const auto previous_begin = starts[decorator_index - 1];
            const auto previous_end = starts[decorator_index];
            const auto previous = source_line(source, previous_begin, previous_end);
            if (!previous.starts_with('@'))
                break;
            block_begin = previous_begin;
            --decorator_index;
        }

        auto block_end = source.size();
        for (std::size_t next = index + 1; next < starts.size(); ++next) {
            const auto next_begin = starts[next];
            const auto next_end = next + 1 < starts.size() ? starts[next + 1]
                                                           : source.size();
            const auto line = source_line(source, next_begin, next_end);
            if (line.empty())
                continue;
            if (line.front() != ' ' && line.front() != '\t') {
                block_end = next_begin;
                break;
            }
        }
        return std::pair{block_begin, block_end};
    }
    return std::nullopt;
}

bool request_header_is(const Request &request, std::string_view name, std::string_view expected) {
    const auto equal_ignore_case = [](std::string_view lhs, std::string_view rhs) {
        return lhs.size() == rhs.size() &&
               std::equal(lhs.begin(), lhs.end(), rhs.begin(),
                          [](uint8_t lhs_char, uint8_t rhs_char) {
                              if (lhs_char >= 'A' && lhs_char <= 'Z')
                                  lhs_char = lhs_char + 'a' - 'A';
                              if (rhs_char >= 'A' && rhs_char <= 'Z')
                                  rhs_char = rhs_char + 'a' - 'A';
                              return lhs_char == rhs_char;
                          });
    };
    for (const auto &[key, value] : request.heads)
        if (equal_ignore_case(key, name) && equal_ignore_case(value, expected))
            return true;
    return false;
}

fs::path case_path(const fs::path &cases_root, std::string_view id) {
    if (id.empty() || id.front() == '.' || id.back() == '.')
        throw std::invalid_argument("Invalid case id");
    fs::path relative;
    std::size_t begin = 0;
    while (begin < id.size()) {
        auto end = id.find('.', begin);
        auto segment = id.substr(
            begin, end == std::string_view::npos ? id.size() - begin : end - begin);
        if (!valid_segment(segment))
            throw std::invalid_argument("Invalid case id");
        relative /= std::string(segment);
        if (end == std::string_view::npos)
            break;
        begin = end + 1;
    }
    if (relative.empty())
        throw std::invalid_argument("Invalid case id");
    relative += ".yml";
    return cases_root / relative;
}

fs::path plan_path(const fs::path &plans_root, std::string_view name) {
    auto value = std::string(name);
    if (value.ends_with(".yml"))
        value.resize(value.size() - 4);
    else if (value.ends_with(".yaml"))
        value.resize(value.size() - 5);
    if (!valid_segment(value))
        throw std::invalid_argument("Invalid plan name");
    return plans_root / (value + ".yml");
}

std::string case_id(const fs::path &cases_root, const fs::path &file) {
    auto relative = fs::relative(file, cases_root);
    relative.replace_extension();
    std::string id;
    for (const auto &part : relative) {
        if (!id.empty())
            id += '.';
        id += part.string();
    }
    return id;
}

std::string yaml_info(const fs::path &path, std::string fallback) {
    const auto content = read_text(path);
    std::size_t begin = 0;
    bool nested_info = false;
    while (begin < content.size()) {
        const auto end = content.find('\n', begin);
        auto line = content.substr(
            begin, end == std::string::npos ? content.size() - begin : end - begin);
        if (!line.empty() && line.back() == '\r')
            line.pop_back();
        if (line.starts_with("info:")) {
            auto value = line.substr(5);
            const auto first = value.find_first_not_of(" \t");
            if (first != std::string::npos) {
                value.erase(0, first);
                const auto last = value.find_last_not_of(" \t");
                value.erase(last + 1);
                if (value.size() >= 2 &&
                    ((value.front() == '\'' && value.back() == '\'') ||
                     (value.front() == '"' && value.back() == '"')))
                    value = value.substr(1, value.size() - 2);
                if (!value.empty())
                    return value;
            }
            nested_info = true;
        } else if (nested_info) {
            if (!line.empty() && line.front() != ' ' && line.front() != '\t')
                break;
            const auto first = line.find_first_not_of(" \t");
            if (first != std::string::npos && line.substr(first).starts_with("name:")) {
                auto value = line.substr(first + 5);
                const auto value_first = value.find_first_not_of(" \t");
                if (value_first != std::string::npos) {
                    value.erase(0, value_first);
                    const auto last = value.find_last_not_of(" \t");
                    value.erase(last + 1);
                    if (value.size() >= 2 &&
                        ((value.front() == '\'' && value.back() == '\'') ||
                         (value.front() == '"' && value.back() == '"')))
                        value = value.substr(1, value.size() - 2);
                    if (!value.empty())
                        return value;
                }
            }
        }
        if (end == std::string::npos)
            break;
        begin = end + 1;
    }
    return fallback;
}

void json_error(Response &res, std::uint16_t status, std::string_view message) {
    res.json({{"error", message}}).status(status);
}

template <typename Fn>
auto guarded(Fn fn) {
    return [fn = std::move(fn)](const Request &req, Response &res) {
        try {
            fn(req, res);
        } catch (const std::invalid_argument &error) {
            json_error(res, 400, error.what());
        } catch (const std::exception &error) {
            json_error(res, 500, error.what());
        }
    };
}

bool yaml_ai_created(const fs::path &path) {
    // AI 交付始终生成独立的顶层布尔字段，不接受字符串 "true"。
    static const std::regex marker(R"((^|\n)ai_created:[ \t]*(true|True|TRUE)[ \t]*(#[^\r\n]*)?(\r?\n|$))");
    return std::regex_search(read_text(path), marker);
}

json list_cases(const fs::path &cases_root) {
    json result = json::array();
    if (!fs::exists(cases_root))
        return result;
    for (const auto &entry : fs::recursive_directory_iterator(cases_root)) {
        if (!entry.is_regular_file() || !is_yaml(entry.path()))
            continue;
        auto id = case_id(cases_root, entry.path());
        auto dot = id.find_last_of('.');
        auto filename = entry.path().stem().string();
        result.push_back({
            {"id", id},
            {"name", yaml_info(entry.path(), filename)},
            {"ai_created", yaml_ai_created(entry.path())},
            {"filename", filename},
            {"folder", dot == std::string::npos ? "" : id.substr(0, dot)},
        });
    }
    std::sort(result.begin(), result.end(), [](const json &lhs, const json &rhs) {
        return lhs.at("id").get<std::string>() < rhs.at("id").get<std::string>();
    });
    return result;
}

json list_plans(const fs::path &plans_root) {
    json result = json::array();
    if (!fs::exists(plans_root))
        return result;
    for (const auto &entry : fs::directory_iterator(plans_root)) {
        if (!entry.is_regular_file() || !is_yaml(entry.path()))
            continue;
        const auto id = entry.path().stem().string();
        result.push_back({
            {"id", id},
            {"name", yaml_info(entry.path(), id)},
            {"ai_created", yaml_ai_created(entry.path())},
            {"filename", entry.path().filename().string()},
        });
    }
    std::sort(result.begin(), result.end(), [](const json &lhs, const json &rhs) {
        return lhs.at("id").get<std::string>() < rhs.at("id").get<std::string>();
    });
    return result;
}

bool valid_log_name(std::string_view name) {
    static const std::regex pattern(
        R"(^summary(?:_[0-9]{8}-[0-9]{6}(?:_[0-9]+)?)?\.log$)");
    return std::regex_match(name.begin(), name.end(), pattern);
}

json list_logs(const fs::path &tmp_root) {
    std::vector<fs::directory_entry> files;
    if (fs::exists(tmp_root))
        for (const auto &entry : fs::directory_iterator(tmp_root))
            if (entry.is_regular_file() && valid_log_name(entry.path().filename().string()))
                files.push_back(entry);
    std::sort(files.begin(), files.end(), [](const auto &lhs, const auto &rhs) {
        return lhs.last_write_time() > rhs.last_write_time();
    });
    json result = json::array();
    for (const auto &entry : files)
        result.push_back({
            {"name", entry.path().filename().string()},
            {"size", entry.file_size()},
        });
    return result;
}

std::string shell_quote(std::string_view value) {
#ifdef _WIN32
    std::string result{"\""};
    for (char ch : value) {
        if (ch == '\"')
            result += '\\';
        result += ch;
    }
    return result + "\"";
#else
    std::string result{"'"};
    for (char ch : value) {
        if (ch == '\'')
            result += "'\\''";
        else
            result += ch;
    }
    return result + "'";
#endif
}

int execute_plans(const fs::path &root, const std::vector<std::string> &plans,
                  const std::optional<std::string> &rmvl_dir, ExecutionJob &job) {
#ifdef _WIN32
    std::string command =
        fmt::format("cd /d {} && python -u {}", shell_quote(root.string()),
                    shell_quote((root / "test.py").string()));
#else
    std::string command =
        fmt::format("cd {} && python3 -u {}", shell_quote(root.string()),
                    shell_quote((root / "test.py").string()));
#endif
    for (const auto &plan : plans) {
        if (!valid_segment(plan))
            throw std::invalid_argument("Invalid plan name");
        command += " " + shell_quote(plan);
    }
    if (rmvl_dir)
        command += " --rmvl-dir " + shell_quote(*rmvl_dir);
    command += " 2>&1";

#ifdef _WIN32
    FILE *pipe = _popen(command.c_str(), "r");
#else
    FILE *pipe = popen(command.c_str(), "r");
#endif
    if (!pipe)
        throw std::runtime_error("Cannot start KDT process");

    std::array<char, 4096> buffer{};
    while (std::fgets(buffer.data(), static_cast<int>(buffer.size()), pipe)) {
        std::scoped_lock lock(job.mutex);
        job.output += buffer.data();
        ++job.revision;
    }

#ifdef _WIN32
    int status = _pclose(pipe);
#else
    int status = pclose(pipe);
    if (WIFEXITED(status))
        status = WEXITSTATUS(status);
    else if (WIFSIGNALED(status))
        status = 128 + WTERMSIG(status);
#endif
    return status;
}

Config parse_args(int argc, char **argv) {
    Config config;
    for (int i = 1; i < argc; ++i) {
        std::string_view arg = argv[i];
        if (arg == "--port" && i + 1 < argc)
            config.port = static_cast<std::uint16_t>(std::stoi(argv[++i]));
        else if (arg == "--with-ai")
            config.with_ai = true;
        else if (arg == "--help" || arg == "-h" || arg == "-?") {
            fmt::println("\033[1mUsage:\033[0m\n   \033[36mrmvl_kdt_agent\033[0m "
                         "\033[90m[--port <port>] [--with-ai] [--help | -h | -?]\033[0m\n");
            fmt::println("\033[1mArguments:\033[0m");
            fmt::println("   \033[36m--port\033[0m           \033[90mPort number to "
                         "listen on (default: 8765)\033[0m");
            fmt::println("   \033[36m--with-ai\033[0m        \033[90mEnable local Codex assistant\033[0m");
            fmt::println(
                "   \033[36m--help\033[0m, \033[36m-h\033[0m, \033[36m-?\033[0m   "
                "\033[90mShow this help message and exit\033[0m");
            std::exit(0);
        } else
            throw std::invalid_argument(
                fmt::format("Unknown or incomplete argument: {}", arg));
    }
    return config;
}

void absent(const Request &, Response &res) {
    res.heads["Access-Control-Allow-Headers"] =
        "Content-Type, Authorization, X-KDT-If-Absent";
}

} // namespace kdt

#include "ai.hpp"

int main(int argc, char **argv) try {
#ifndef _WIN32
    std::signal(SIGPIPE, SIG_IGN);
#endif
    auto config = kdt::parse_args(argc, argv);
    const fs::path root{path::root};
    auto components = root / "components";
    auto cases = root / "cases";
    auto plans = root / "plans";
    auto tmp = root / "tmp";

    if (!fs::is_regular_file(components / "keywords.yml"))
        throw std::runtime_error("keywords.yml not found under KDT root");

    rm::async::IOContext context;
    rm::async::Webapp app(context);
    rm::async::HttpServer server(app);
    kdt::ExecutionJob execution;
    kdt::AiJob ai;
    kdt::register_ai(app, context, root, ai, execution, config.with_ai);

    app.get("/", [](const Request &, Response &res) {
        res.send(
            "<!DOCTYPE html><html lang=\"en\"><head><meta charset=\"utf-8\"><style>body { font-family: ui-monospace, \"SFMono-Regular"
            "\", \"Cascadia Mono\", \"Ubuntu Mono\", Menlo, Monaco, Consolas, \"Liberation Mono\", \"Courier New\", monospace; }</style>"
            "<title>Error</title></head><body><h3>Unsupported Operation</h3><p>KDT Agent does not serve the web interface. To access "
            "the web interface, please visit <a href=\"https://cv-rmvl.github.io/kdt/\">RMVL KDT Studio</a>.</p></body></html>");
    });

    app.get("/api/health", [](const Request &, Response &res) {
        res.json({{"ok", true}});
    });
    app.get("/api/keywords", kdt::guarded([&](const Request &, Response &res) {
                res.json({{"content", kdt::read_text(components / "keywords.yml")}});
            }));
    app.post("/api/keywords", kdt::guarded([&](const Request &req, Response &res) {
                 kdt::write_text(components / "keywords.yml", req.body);
                 res.json({{"ok", true}});
             }));
    app.get("/api/components/:component", kdt::guarded([&](const Request &req, Response &res) {
                const auto target = kdt::component_target(
                    components, req.params.at("component"));
                if (!fs::is_regular_file(target.file))
                    return kdt::json_error(res, 404, "Python component file not found");
                res.json({
                    {"content", kdt::read_text(target.file)},
                    {"path", fs::relative(target.file, components).generic_string()},
                    {"function", target.function},
                });
            }));
    app.post("/api/components/:component", kdt::guarded([&](const Request &req, Response &res) {
                 const auto target = kdt::component_target(
                     components, req.params.at("component"));
                 if (!kdt::top_level_function_range(req.body, target.function))
                     throw std::invalid_argument(
                         "Saved source must contain the bound top-level function");
                 kdt::write_text(target.file, req.body);
                 res.json({{"ok", true}});
             }));
    app.post("/api/components/:component/append", kdt::guarded([&](const Request &req, Response &res) {
                 const auto target = kdt::component_target(
                     components, req.params.at("component"));
                 auto source = fs::exists(target.file) ? kdt::read_text(target.file) : "";
                 if (kdt::top_level_function_range(source, target.function))
                     return kdt::json_error(res, 409, "Python function already exists");
                 if (!kdt::top_level_function_range(req.body, target.function))
                     throw std::invalid_argument("Missing bound top-level function in template");
                 kdt::write_text(target.file, source + "\n\n" + req.body);
                 res.json({{"ok", true}});
             }));
    app.del("/api/components/:component", kdt::guarded([&](const Request &req, Response &res) {
                const auto target = kdt::component_target(
                    components, req.params.at("component"));
                if (!fs::is_regular_file(target.file))
                    return kdt::json_error(res, 404, "Python component file not found");

                auto source = kdt::read_text(target.file);
                const auto range = kdt::top_level_function_range(
                    source, target.function);
                if (!range)
                    return kdt::json_error(res, 404, "Bound Python function not found");
                source.erase(range->first, range->second - range->first);
                if (source.find_first_not_of(" \t\r\n") == std::string::npos)
                    fs::remove(target.file);
                else
                    kdt::write_text(target.file, source);
                res.json({{"ok", true}, {"removed", target.function}});
            }));
    app.get("/api/cases", kdt::guarded([&](const Request &, Response &res) {
                res.json(kdt::list_cases(cases));
            }));
    app.get("/api/cases/:id", kdt::guarded([&](const Request &req, Response &res) {
                auto path = kdt::case_path(cases, req.params.at("id"));
                if (!fs::is_regular_file(path))
                    return kdt::json_error(res, 404, "Case not found");
                res.json({{"content", kdt::read_text(path)}});
            }));
    app.post("/api/cases/:id", kdt::guarded([&](const Request &req, Response &res) {
                 auto path = kdt::case_path(cases, req.params.at("id"));
                 if (kdt::request_header_is(req, "X-KDT-If-Absent", "true") &&
                     fs::exists(path))
                     return kdt::json_error(res, 409, "Case name already exists");
                 kdt::write_text(path, req.body);
                 res.json({{"ok", true}});
             }));
    app.del("/api/cases/:id", kdt::guarded([&](const Request &req, Response &res) {
                auto path = kdt::case_path(cases, req.params.at("id"));
                if (fs::exists(path)) {
                    fs::remove(path);
                    kdt::prune_empty_parents(path, cases);
                }
                res.json({{"ok", true}});
            }));

    app.get("/api/plans", kdt::guarded([&](const Request &, Response &res) {
                res.json(kdt::list_plans(plans));
            }));
    app.get("/api/plans/:name", kdt::guarded([&](const Request &req, Response &res) {
                auto path = kdt::plan_path(plans, req.params.at("name"));
                if (!fs::is_regular_file(path))
                    return kdt::json_error(res, 404, "Plan not found");
                res.json({{"content", kdt::read_text(path)}});
            }));
    app.post("/api/plans/:name", kdt::guarded([&](const Request &req, Response &res) {
                 auto path = kdt::plan_path(plans, req.params.at("name"));
                 if (kdt::request_header_is(req, "X-KDT-If-Absent", "true") &&
                     fs::exists(path))
                     return kdt::json_error(res, 409, "Plan name already exists");
                 kdt::write_text(path, req.body);
                 res.json({{"ok", true}});
             }));
    app.del("/api/plans/:name", kdt::guarded([&](const Request &req, Response &res) {
                auto path = kdt::plan_path(plans, req.params.at("name"));
                if (fs::exists(path))
                    fs::remove(path);
                res.json({{"ok", true}});
            }));

    app.post("/api/execute", kdt::guarded([&](const Request &req, Response &res) {
                 auto body = json::parse(req.body);
                 auto selected = body.at("plans").get<std::vector<std::string>>();
                 std::optional<std::string> rmvl_dir;
                 if (selected.empty())
                     throw std::invalid_argument("Select at least one plan");
                 for (const auto &plan : selected)
                     if (!kdt::valid_segment(plan))
                         throw std::invalid_argument("Invalid plan name");
                 if (body.contains("rmvlDir") && !body.at("rmvlDir").is_null()) {
                     if (!body.at("rmvlDir").is_string())
                         throw std::invalid_argument("RMVL directory must be a string");
                     auto value = body.at("rmvlDir").get<std::string>();
                     if (!value.empty()) {
                         if (!fs::is_directory(kdt::path_from_utf8(value)))
                             throw std::invalid_argument("RMVL directory does not exist");
                         rmvl_dir = std::move(value);
                     }
                 }
                 {
                     std::scoped_lock lock(execution.mutex, ai.mutex);
                     if (execution.running || ai.running)
                         return kdt::json_error(res, 409, "A test execution is already running");
                     execution.output.clear();
                     execution.running = true;
                     execution.finished = false;
                     execution.ok = false;
                     execution.exit_code = 0;
                     ++execution.revision;
                 }

                 execution.task = std::async(std::launch::async, [
                     &execution, root, selected = std::move(selected),
                     rmvl_dir = std::move(rmvl_dir)
                 ] {
                     int status = 1;
                     try {
                         status = execute_plans(root, selected, rmvl_dir, execution);
                     } catch (const std::exception &error) {
                         std::scoped_lock lock(execution.mutex);
                         execution.output += fmt::format("[erro] {}\n", error.what());
                         ++execution.revision;
                     }
                     std::scoped_lock lock(execution.mutex);
                     execution.exit_code = status;
                     execution.ok = status == 0;
                     execution.running = false;
                     execution.finished = true;
                     ++execution.revision;
                 });
                 res.json({{"started", true}});
             }));

    app.ws("/api/execute/ws", [&execution, &context](rm::async::WebSocket &ws, const Request &) -> rm::async::Task<> {
        rm::async::Timer timer(context);
        std::uint64_t sent_revision{};
        std::size_t sent_output_size{};
        bool initialized{};
        std::size_t heartbeat_ticks{};
        while (ws.is_open()) {
            json message;
            bool should_send{};
            {
                std::scoped_lock lock(execution.mutex);
                if (!initialized) {
                    message = {
                        {"type", "state"},
                        {"running", execution.running},
                        {"finished", execution.finished},
                        {"ok", execution.ok},
                        {"exitCode", execution.exit_code},
                        {"output", execution.output},
                    };
                    sent_output_size = execution.output.size();
                    sent_revision = execution.revision;
                    initialized = true;
                    should_send = true;
                } else if (sent_revision != execution.revision) {
                    const bool output_reset = execution.output.size() < sent_output_size;
                    message = {
                        {"type", output_reset ? "state" : "update"},
                        {"running", execution.running},
                        {"finished", execution.finished},
                        {"ok", execution.ok},
                        {"exitCode", execution.exit_code},
                    };
                    if (output_reset)
                        message["output"] = execution.output;
                    else
                        message["append"] = execution.output.substr(sent_output_size);
                    sent_output_size = execution.output.size();
                    sent_revision = execution.revision;
                    should_send = true;
                } else if (heartbeat_ticks >= 50) {
                    message = {{"type", "heartbeat"}};
                    should_send = true;
                }
            }
            if (should_send) {
                heartbeat_ticks = 0;
                const auto payload = message.dump();
                if (!(co_await ws.send(payload)))
                    co_return;
            }
            ++heartbeat_ticks;
            co_await timer.sleep_for(100ms);
        }
    });

    app.get("/api/logs", kdt::guarded([&](const Request &, Response &res) {
                {
                    std::scoped_lock lock(execution.mutex);
                    if (execution.running)
                        return kdt::json_error(res, 409, "Logs are unavailable during execution");
                }
                res.json(kdt::list_logs(tmp));
            }));

    app.del("/api/logs", kdt::guarded([&](const Request &, Response &res) {
                {
                    std::scoped_lock lock(execution.mutex);
                    if (execution.running)
                        return kdt::json_error(res, 409, "Logs cannot be cleared during execution");
                }
                std::size_t removed{};
                if (fs::exists(tmp)) {
                    for (const auto &entry : fs::directory_iterator(tmp)) {
                        if (!entry.is_regular_file() ||
                            !kdt::valid_log_name(entry.path().filename().string()))
                            continue;
                        if (fs::remove(entry.path()))
                            ++removed;
                    }
                }
                res.json({{"ok", true}, {"removed", removed}});
            }));

    app.get("/api/logs/:name", kdt::guarded([&](const Request &req, Response &res) {
                {
                    std::scoped_lock lock(execution.mutex);
                    if (execution.running)
                        return kdt::json_error(res, 409, "Logs are unavailable during execution");
                }
                const auto &name = req.params.at("name");
                if (!kdt::valid_log_name(name))
                    throw std::invalid_argument("Invalid log name");
                auto path = tmp / name;
                if (!fs::is_regular_file(path))
                    return kdt::json_error(res, 404, "Log not found");
                res.json({{"content", kdt::read_text(path)}});
            }));

    app.use(rm::cors());
    app.use(kdt::absent);

    server.listen(config.port, [&] {
        fmt::println(
            "KDT Agent 正在监听 \033[32mhttp://127.0.0.1:{}\033[0m ，根目录: {}",
            config.port, root.string());
    });
    rm::async::co_spawn(context, &rm::async::HttpServer::spin, &server);
    context.run();
    {
        std::scoped_lock lock(ai.mutex);
        if (ai.running)
            kdt::write_text(ai.directory / "cancel", "cancel");
    }
    return 0;
} catch (const std::exception &error) {
    fmt::println(stderr, "rmvl_kdt_agent: {}", error.what());
    return 1;
}

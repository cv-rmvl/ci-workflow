#pragma once

// 本文件在 Agent 的通用文件和 Shell 辅助函数定义之后引入。
namespace kdt {

std::int64_t ai_now_ms() {
    return std::chrono::duration_cast<std::chrono::milliseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
}

struct AiJob {
    std::mutex mutex;
    std::future<void> task;
    std::future<void> quota_task;
    fs::path directory;
    fs::path quota_stop;
    bool running{};
    std::uint64_t revision{};
    json state = {{"phase", "idle"}, {"category", nullptr}, {"completedPhases", json::array()},
                  {"messages", json::array()}, {"logs", json::array()},
                  {"timing", {{"startedAt", 0}, {"lastOutputAt", 0}, {"finishedAt", 0}}},
                  {"files", json::array()}, {"questions", json::array()}, {"conclusion", ""},
                  {"dryrun", nullptr}, {"session", nullptr}, {"tokens", {{"used", 0}}},
                  {"catalog", {{"models", json::array()}, {"defaultModel", ""}, {"defaultEffort", ""}}},
                  {"quota", {{"remainingPercent", nullptr}, {"windows", json::array()}}}};

    ~AiJob() {
        if (!directory.empty())
            std::ofstream(directory / "cancel").write("cancel", 6);
        if (!quota_stop.empty())
            std::ofstream(quota_stop).write("stop", 4);
        if (task.valid())
            task.wait();
        if (quota_task.valid())
            quota_task.wait();
    }
};

bool ai_transition_allowed(std::string_view from, std::string_view to) {
    if (to == "failed" || to == "cancelled")
        return true;
    return (from == "idle" && to == "analysis") ||
           (from == "analysis" && (to == "boundary" || to == "completed")) ||
           (from == "boundary" && (to == "coding" || to == "review")) ||
           (from == "coding" && to == "dryrun") ||
           (from == "dryrun" && to == "review") ||
           (from == "review" && (to == "coding" || to == "completed"));
}

void ai_event(AiJob &job, const json &event) {
    std::scoped_lock lock(job.mutex);
    const auto type = event.value("type", "");
    if (job.running && type != "quota" && type != "catalog")
        job.state["timing"]["lastOutputAt"] = ai_now_ms();
    if (type == "phase" || type == "terminal") {
        const auto next = event.at("phase").get<std::string>();
        const auto previous = job.state.at("phase").get<std::string>();
        if (!ai_transition_allowed(previous, next))
            throw std::invalid_argument("Invalid AI workflow transition");
        auto &completed = job.state["completedPhases"];
        if (previous == "review" && next == "coding") {
            completed.erase(std::remove_if(completed.begin(), completed.end(), [](const json &phase) {
                return phase == "coding" || phase == "dryrun" || phase == "review";
            }), completed.end());
        } else if (previous != "idle" && next != "failed" && next != "cancelled") {
            if (std::find(completed.begin(), completed.end(), previous) == completed.end())
                completed.push_back(previous);
        }
        job.state["phase"] = next;
        if (next == "dryrun")
            job.state["dryrun"] = nullptr;
        if (event.contains("error"))
            job.state["error"] = event.at("error");
    } else if (type == "category")
        job.state["category"] = event.at("category");
    else if (type == "session")
        job.state["session"] = event.at("session");
    else if (type == "message")
        job.state["messages"].push_back(event);
    else if (type == "progress") {
        auto &messages = job.state["messages"];
        const auto id = event.at("id").get<std::string>();
        const auto found = std::find_if(messages.begin(), messages.end(), [&id](const json &message) {
            return message.value("id", "") == id;
        });
        if (found == messages.end())
            messages.push_back(event);
        else
            *found = event;
        if (messages.size() > 300)
            messages.erase(messages.begin());
    } else if (type == "questions")
        job.state["questions"] = event.at("questions");
    else if (type == "conclusion")
        job.state["conclusion"] = event.at("text");
    else if (type == "files")
        job.state["files"] = event.at("files");
    else if (type == "dryrun")
        job.state["dryrun"] = event.at("results");
    else if (type == "tokens")
        job.state["tokens"] = event;
    else if (type == "quota")
        job.state["quota"] = event.at("quota");
    else if (type == "catalog")
        job.state["catalog"] = event.at("catalog");
    else if (type == "log") {
        job.state["logs"].push_back(event);
        if (job.state["logs"].size() > 1200)
            job.state["logs"].erase(job.state["logs"].begin());
    }
    ++job.revision;
}

void run_ai(const fs::path &root, const fs::path &request, AiJob &job, bool monitor = false) {
    try {
#ifdef _WIN32
        const auto command = "python -u " + shell_quote((root / "agents/ai_worker.py").string()) +
                             (monitor ? " --quota-watch " : " ") + shell_quote(request.string()) + " 2>&1";
        FILE *pipe = _popen(command.c_str(), "r");
#else
        const auto command = "python3 -u " + shell_quote((root / "agents/ai_worker.py").string()) +
                             (monitor ? " --quota-watch " : " ") + shell_quote(request.string()) + " 2>&1";
        FILE *pipe = popen(command.c_str(), "r");
#endif
        if (!pipe)
            throw std::runtime_error("Cannot start AI bridge");
        std::array<char, 4096> buffer{};
        std::string pending;
        while (std::fgets(buffer.data(), static_cast<int>(buffer.size()), pipe)) {
            pending += buffer.data();
            if (pending.empty() || pending.back() != '\n')
                continue;
            auto event = json::parse(pending, nullptr, false);
            if (event.is_discarded())
                ai_event(job, {{"type", "log"}, {"level", "warn"}, {"text", pending}});
            else {
                try {
                    ai_event(job, event);
                } catch (const std::exception &error) {
                    ai_event(job, {{"type", "log"}, {"level", "erro"}, {"text", error.what()}});
                }
            }
            pending.clear();
        }
#ifdef _WIN32
        const auto status = _pclose(pipe);
#else
        const auto status = pclose(pipe);
#endif
        if (monitor)
            return;
        std::scoped_lock lock(job.mutex);
        if (status != 0 || job.state["phase"] != "completed") {
            if (job.state["phase"] != "cancelled") {
                job.state["phase"] = "failed";
                if (!job.state.contains("error"))
                    job.state["error"] = "AI bridge terminated before completion";
            }
        }
    } catch (const std::exception &error) {
        if (monitor) {
            ai_event(job, {{"type", "quota"}, {"quota", {{"remainingPercent", nullptr}, {"windows", json::array()}, {"error", error.what()}}}});
            return;
        }
        std::scoped_lock lock(job.mutex);
        job.state["phase"] = "failed";
        job.state["error"] = error.what();
    }
    std::scoped_lock lock(job.mutex);
    job.running = false;
    job.state["timing"]["finishedAt"] = ai_now_ms();
    for (auto &message : job.state["messages"])
        if (message.value("status", "") == "running")
            message["status"] = job.state["phase"] == "cancelled" ? "cancelled" : "interrupted";
    ++job.revision;
}

fs::path ai_archive_path(const fs::path &root, const std::string &key) {
    static const std::regex valid_key("^[0-9a-f]{8}$");
    if (!std::regex_match(key, valid_key))
        throw std::invalid_argument("Invalid AI session key");
    const auto build = root.parent_path() / "build";
    const auto directory = build / ("ai_assisstant-" + key);
    const auto archive = directory / "delivery.json";
    if (!fs::exists(archive) || fs::is_symlink(build) || fs::is_symlink(directory) || fs::is_symlink(archive) ||
        !fs::is_directory(directory) || !fs::is_regular_file(archive) ||
        fs::canonical(archive) != archive.lexically_normal() || fs::file_size(archive) > 8 * 1024 * 1024)
        throw std::invalid_argument("AI session archive is missing, unsafe or too large");
    return archive;
}

json ai_read_archive(const fs::path &root, const std::string &key) try {
    auto archive = json::parse(read_text(ai_archive_path(root, key)));
    if (!archive.is_object() || archive.value("schemaVersion", 0) != 1 ||
        !archive.contains("session") || !archive["session"].is_object() ||
        archive["session"].value("key", "") != key ||
        !archive["session"].contains("summary") || !archive["session"]["summary"].is_string() ||
        archive["session"]["summary"].get<std::string>().empty() ||
        archive["session"]["summary"].get<std::string>().size() > 512)
        throw std::invalid_argument("Invalid AI session archive metadata");
    const auto phase = archive.value("phase", "");
    const auto category = archive.value("category", "");
    if ((phase != "completed" && phase != "failed" && phase != "cancelled") ||
        (category != "development" && category != "design" && category != "consultation" && category != "out_of_scope"))
        throw std::invalid_argument("Only finished AI sessions can be loaded");
    for (const auto key : {"prompt", "conclusion", "error"})
        if (archive.contains(key) && (!archive[key].is_string() || archive[key].get<std::string>().size() > 20000))
            throw std::invalid_argument("Invalid AI archive text field");
    for (const auto key : {"files", "questions"})
        if (!archive.contains(key) || !archive[key].is_array() || archive[key].size() > 500)
            throw std::invalid_argument("Invalid AI archive delivery list");
    for (const auto &question : archive["questions"])
        if (!question.is_string())
            throw std::invalid_argument("Invalid AI archive question");
    for (const auto &file : archive["files"])
        if (!file.is_object() || !file.contains("path") || !file["path"].is_string() ||
            (file.value("status", "") != "added" && file.value("status", "") != "modified"))
            throw std::invalid_argument("Invalid AI archive file entry");
    if (archive.contains("dryrun") && !archive["dryrun"].is_null()) {
        const auto &dryrun = archive["dryrun"];
        if (!dryrun.is_object())
            throw std::invalid_argument("Invalid AI archive DryRun");
        for (const auto key : {"regression", "functional"}) {
            if (!dryrun.contains(key) || !dryrun[key].is_object())
                throw std::invalid_argument("Invalid AI archive test counts");
            for (const auto field : {"total", "passed", "failed", "skipped"})
                if (!dryrun[key].contains(field) || !dryrun[key][field].is_number_integer() || dryrun[key][field] < 0)
                    throw std::invalid_argument("Invalid AI archive test count");
        }
        if (!dryrun.contains("checks") || !dryrun["checks"].is_array() || dryrun["checks"].size() > 500)
            throw std::invalid_argument("Invalid AI archive checks");
        for (const auto &check : dryrun["checks"])
            if (!check.is_object() || !check.contains("name") || !check["name"].is_string() ||
                !check.contains("status") || !check["status"].is_string())
                throw std::invalid_argument("Invalid AI archive check");
    }
    // 不信任归档里给出的目录，始终从受限 key 推导。
    archive["session"]["directory"] = ai_archive_path(root, key).parent_path().string();
    return archive;
} catch (const json::exception &) {
    throw std::invalid_argument("Invalid AI session archive JSON");
}

void register_ai(rm::async::Webapp &app, rm::async::IOContext &context,
                 const fs::path &root, AiJob &job, ExecutionJob &execution, bool enabled) {
    if (enabled) {
        auto directory = root / "tmp/ai" / ("quota-" + std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count()));
        fs::create_directories(directory);
        job.quota_stop = directory / "stop";
        job.quota_task = std::async(std::launch::async, [&job, root] {
            run_ai(root, job.quota_stop, job, true);
        });
    }
    app.get("/api/ai/status", guarded([&job, enabled](const Request &, Response &res) {
        std::scoped_lock lock(job.mutex);
        auto state = job.state;
        state["enabled"] = enabled;
        state["running"] = job.running;
        state["provider"] = "Codex CLI";
        state["timing"]["serverNow"] = ai_now_ms();
        res.json(state);
    }));
    app.get("/api/ai/sessions", guarded([root, enabled](const Request &, Response &res) {
        if (!enabled)
            return json_error(res, 403, "AI is disabled");
        json sessions = json::array();
        const auto build = root.parent_path() / "build";
        if (fs::is_directory(build) && !fs::is_symlink(build))
            for (const auto &entry : fs::directory_iterator(build)) {
                const auto name = entry.path().filename().string();
                if (!name.starts_with("ai_assisstant-") || name.size() != 22)
                    continue;
                try {
                    const auto key = name.substr(14);
                    auto archive = ai_read_archive(root, key);
                    const auto modified = std::chrono::duration_cast<std::chrono::milliseconds>(
                        fs::file_time_type::clock::to_sys(fs::last_write_time(entry.path() / "delivery.json")).time_since_epoch()).count();
                    auto created = modified;
                    if (archive.contains("timing") && archive["timing"].is_object() &&
                        archive["timing"].contains("startedAt") && archive["timing"]["startedAt"].is_number_integer() &&
                        archive["timing"]["startedAt"] > 0)
                        created = archive["timing"]["startedAt"].get<std::int64_t>();
                    sessions.push_back({{"key", key}, {"summary", archive["session"]["summary"]},
                                        {"phase", archive["phase"]}, {"category", archive["category"]},
                                        {"createdAt", created}});
                } catch (const std::exception &) {
                    // 损坏或旧格式归档不影响其他会话，也不暴露文件内容。
                }
            }
        std::sort(sessions.begin(), sessions.end(), [](const json &lhs, const json &rhs) {
            if (lhs["createdAt"] == rhs["createdAt"])
                return lhs["key"].get<std::string>() > rhs["key"].get<std::string>();
            return lhs["createdAt"].get<std::int64_t>() > rhs["createdAt"].get<std::int64_t>();
        });
        res.json(sessions);
    }));
    app.del("/api/ai/sessions/:key", guarded([&job, &execution, root, enabled](const Request &req, Response &res) {
        if (!enabled)
            return json_error(res, 403, "AI is disabled");
        std::scoped_lock lock(job.mutex, execution.mutex);
        if (job.running || execution.running)
            return json_error(res, 409, "请等待当前 AI 任务或测试执行结束");
        // 删除历史记录，不递归删除构建目录、测试证据和用户源码。
        const auto archive = ai_archive_path(root, req.params.at("key"));
        if (!fs::remove(archive))
            return json_error(res, 404, "AI session archive not found");
        res.json({{"ok", true}});
    }));
    app.get("/api/ai/sessions/:key", guarded([&job, &execution, root, enabled](const Request &req, Response &res) {
        if (!enabled)
            return json_error(res, 403, "AI is disabled");
        std::scoped_lock lock(job.mutex, execution.mutex);
        if (job.running || execution.running)
            return json_error(res, 409, "请等待当前 AI 任务或测试执行结束");
        const auto archive = ai_read_archive(root, req.params.at("key"));
        auto state = AiJob{}.state;
        state["quota"] = job.state["quota"];
        state["catalog"] = job.state["catalog"];
        for (const auto key : {"phase", "category", "session", "prompt", "conclusion", "files", "questions", "dryrun", "error"})
            if (archive.contains(key))
                state[key] = archive[key];
        if (archive.contains("timing") && archive["timing"].is_object())
            for (const auto key : {"startedAt", "lastOutputAt", "finishedAt"})
                if (archive["timing"].contains(key) && archive["timing"][key].is_number_integer())
                    state["timing"][key] = archive["timing"][key];
        state["archived"] = true;
        if (state["phase"] == "completed") {
            if (state["category"] == "development")
                state["completedPhases"] = {"analysis", "boundary", "coding", "dryrun", "review"};
            else if (state["category"] == "design")
                state["completedPhases"] = {"analysis", "boundary", "review"};
            else
                state["completedPhases"] = {"analysis"};
        }
        state["logs"].push_back({{"type", "log"}, {"level", "info"}, {"text", "[ai.archive] 已加载交付归档，未重新执行历史命令"}});
        state["enabled"] = enabled;
        state["running"] = false;
        state["provider"] = "Codex CLI";
        state["timing"]["serverNow"] = ai_now_ms();
        // 历史浏览是独立快照，不覆盖当前工作区，不向实时通道广播历史状态。
        res.json(state);
    }));
    app.post("/api/ai/tasks", guarded([&job, &execution, root, enabled](const Request &req, Response &res) {
        if (!enabled)
            return json_error(res, 403, "请使用 --with-ai 启动 KDT Agent");
        auto request = json::parse(req.body);
        const auto prompt = request.at("prompt").get<std::string>();
        if (prompt.empty() || prompt.size() > 20000)
            throw std::invalid_argument("Invalid prompt");
        std::scoped_lock lock(job.mutex, execution.mutex);
        if (job.running || execution.running)
            return json_error(res, 409, "请等待当前 AI 任务或测试执行结束");
        for (const auto key : {"model", "effort"})
            if (request.contains(key) && (!request.at(key).is_string() || request.at(key).get<std::string>().size() > 200))
                throw std::invalid_argument("Invalid AI model or reasoning effort");
        const auto model = request.value("model", "");
        auto effort = request.value("effort", "");
        const auto &catalog = job.state.at("catalog");
        if (!model.empty() || !effort.empty()) {
            const auto effective = model.empty() ? catalog.value("defaultModel", "") : model;
            const auto &models = catalog.at("models");
            const auto selected = std::find_if(models.begin(), models.end(), [&](const json &item) {
                return item.at("value") == effective;
            });
            if (selected == models.end())
                throw std::invalid_argument("所选模型不在本机 Codex 模型列表中，请刷新后重试");
            if (effort.empty())
                effort = selected->value("defaultEffort", "");
            const auto &efforts = selected->at("efforts");
            if (!effort.empty() && std::find(efforts.begin(), efforts.end(), effort) == efforts.end())
                throw std::invalid_argument("所选模型不支持该推理强度");
        }
        request["model"] = model;
        request["effort"] = effort;
        job.directory = root / "tmp/ai" / std::to_string(
            std::chrono::system_clock::now().time_since_epoch().count());
        fs::create_directories(job.directory);
        request["directory"] = job.directory.string();
        write_text(job.directory / "request.json", request.dump());
        const auto quota = job.state.at("quota");
        const auto models = job.state.at("catalog");
        job.state = AiJob{}.state;
        job.state["quota"] = quota;
        job.state["catalog"] = models;
        job.state["prompt"] = prompt;
        job.state["options"] = {{"model", model}, {"effort", effort}};
        const auto now = ai_now_ms();
        job.state["timing"] = {{"startedAt", now}, {"lastOutputAt", now}, {"finishedAt", 0}};
        job.running = true;
        ++job.revision;
        job.task = std::async(std::launch::async, [&job, root, path = job.directory / "request.json"] {
            run_ai(root, path, job);
        });
        res.json({{"started", true}});
    }));
    app.post("/api/ai/cancel", guarded([&job, enabled](const Request &, Response &res) {
        if (!enabled)
            return json_error(res, 403, "AI is disabled");
        std::scoped_lock lock(job.mutex);
        if (job.running)
            write_text(job.directory / "cancel", "cancel");
        res.json({{"ok", true}});
    }));
    app.ws("/api/ai/ws", [&job, &context, enabled](rm::async::WebSocket &ws, const Request &) -> rm::async::Task<> {
        rm::async::Timer timer(context);
        std::uint64_t sent{};
        unsigned ticks{};
        bool initialized{};
        while (ws.is_open()) {
            json state;
            {
                std::scoped_lock lock(job.mutex);
                if (!initialized || sent != job.revision) {
                    state = job.state;
                    state["enabled"] = enabled;
                    state["running"] = job.running;
                    state["provider"] = "Codex CLI";
                    state["timing"]["serverNow"] = ai_now_ms();
                    sent = job.revision;
                    initialized = true;
                }
            }
            if (!state.is_null() || ++ticks >= 50) {
                const auto payload = state.is_null() ? json({{"type", "heartbeat"}}).dump() : state.dump();
                if (!(co_await ws.send(payload)))
                    co_return;
                ticks = 0;
            }
            co_await timer.sleep_for(200ms);
        }
    });
}

} // namespace kdt

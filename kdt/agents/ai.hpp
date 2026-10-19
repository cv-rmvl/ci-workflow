#pragma once

// 本文件在 Agent 的通用文件和 Shell 辅助函数定义之后引入。
namespace kdt {

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
                  {"files", json::array()}, {"questions", json::array()},
                  {"dryrun", nullptr}, {"tokens", {{"used", 0}}},
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
    else if (type == "message")
        job.state["messages"].push_back(event);
    else if (type == "questions")
        job.state["questions"] = event.at("questions");
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
    ++job.revision;
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

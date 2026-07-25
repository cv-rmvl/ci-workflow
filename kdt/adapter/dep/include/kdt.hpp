#pragma once

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <future>
#include <string>
#include <string_view>
#include <thread>
#include <unordered_map>
#include <utility>
#include <vector>

namespace kdt {

//! Adapter 使用该退出码通知 KDT 当前功能在本构建配置下不可用
constexpr int skipped_exit_code = 77;

//! C++17 同步 LPSS Adapter 的中断状态
inline volatile std::sig_atomic_t interrupt_requested = 0;

//! 安装 SIGINT 处理器
inline void install_interrupt_handler() {
    interrupt_requested = 0;
    const auto handler = [](int) { interrupt_requested = 1; };
    std::signal(SIGINT, handler);
#ifdef SIGBREAK
    // Windows 只能将 CTRL_BREAK_EVENT 可靠地定向到指定进程组。
    std::signal(SIGBREAK, handler);
#endif
}

//! 判断是否仍应继续运行
inline bool keep_running() noexcept { return interrupt_requested == 0; }

/**
 * @brief 阻塞至收到 SIGINT，并直接结束当前 Adapter 进程
 *
 * C++17 版同步 DataReader 持有永久阻塞的读取线程，无法通过公开接口安全
 * 回收。收到停止信号后跳过静态和栈对象析构，由操作系统统一释放资源。
 */
inline void wait_for_interrupt() {
    install_interrupt_handler();
    while (keep_running())
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    std::fflush(nullptr);
    std::_Exit(0);
}

/**
 * @brief 在线程中执行操作，并在超时后立即终止当前 adapter 进程
 *
 * 阻塞系统调用无法通过标准 C++ 安全取消，因此超时时直接结束 adapter
 * 进程，由操作系统回收工作线程及其持有的设备句柄。
 *
 * @param[in] operation 返回进程退出码的操作
 * @param[in] timeout 最大等待时间
 * @param[in] name 用于超时错误信息的操作名称
 * @return 操作返回的进程退出码
 */
template <typename Fn>
int run_with_timeout(Fn &&operation, std::chrono::milliseconds timeout, std::string_view name) {
    std::packaged_task<int()> task(std::forward<Fn>(operation));
    auto result = task.get_future();
    std::thread worker(std::move(task));

    if (result.wait_for(timeout) == std::future_status::timeout) {
        std::fprintf(stderr, "%.*s timed out after %lld ms\n", static_cast<int>(name.size()), name.data(), static_cast<long long>(timeout.count()));
        std::fflush(stderr);
        std::_Exit(124);
    }

    worker.join();
    return result.get();
}

//! 命令行参数解析器
class command_line_parser {
public:
    command_line_parser(int argc, char **argv, std::string_view keys);

    /**
     * @brief 解析并校验命令行参数
     *
     * @return 参数是否满足要求
     */
    bool parse();

    /**
     * @brief 获取命令行用法
     *
     * @return 形如 `Usage: ./app <key1> <key2>` 的字符串
     */
    std::string usage() const;

    /**
     * @brief 获取指定 Key 的参数值
     *
     * @param[in] key 所需参数的键
     * @return 指定的命令行参数
     */
    std::string get(std::string_view key) const;

    /**
     * @brief 获取指定索引的参数值
     *
     * @param[in] index 所需参数的索引
     * @return 指定的命令行参数
     */
    std::string get(int index) const;

private:
    int _argc{};
    char **_argv{};
    std::string _keys;
    std::vector<std::string> _arguments;
    std::unordered_map<std::string, std::size_t> _key_to_index;
};

} // namespace kdt

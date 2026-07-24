# ci-workflow

此仓库服务于 [RMVL](https://github.com/cv-rmvl/rmvl)。

## 简介

本仓库包含 CI 工作流程的配置和脚本，当触发 push、pull request 或 release 事件时，GitHub Actions 会自动执行构建、测试和部署等任务。

## KDT Agent

KDT Agent 使用 RMVL IO 模块的 `Webapp` 和 `HttpServer` 提供网页与文件系统接口，前端静态页面已部署至 <https://cv-rmvl.github.io/kdt>。

### 构建 Agent

```bash
cmake -S kdt/agents -B build/agents
cmake --build build/agents --parallel
```

### 启动

```bash
./build/agents/rmvl_kdt_agent
```

然后访问 <http://localhost:8765> 即可，也可以指定 `--port` 参数来修改端口号，例如

```bash
./build/agents/rmvl_kdt_agent --port 8080
```

此时可通过访问 <http://localhost:8080> 来访问 KDT Agent。
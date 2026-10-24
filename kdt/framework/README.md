# 框架公共接口

## 全局配置 kdt_config

```python
from framework import kdt_config

system = kdt_config.system          # win、linux、macos，其他系统使用小写系统名
standard = kdt_config.cpp_standard  # 17、20、23、26，尚未构建查询 Adapter 时为 None
```

配置在运行时确定，不需要提前填写配置文件。`system` 是运行 Python/Agent 的系统，不是打开网页的浏览器系统。

`kdt.config.ext` 是运行时扩展信息查询程序，只提供 Python 无法直接获取的额外信息，不重复提供 Python 能自行取得的系统类型。首次读取扩展字段时运行 `adapter/bin/kdt.config.ext`（Windows 为 `.exe`），由 Python 框架解析 JSON、校验字段后合入 `kdt_config` 并缓存。目前输出：

```json
{"cpp_standard": 23}
```

`cpp_standard` 描述 KDT Adapter 实际使用的编译标准，不是系统编译器支持的最高标准，也不代表所有 RMVL 可选功能均可用。具体能力仍以对应 Adapter 的结果为准。后续增加扩展信息时，在此 JSON 对象增加字段，并在 Python 框架增加相应字段的校验及访问接口，不直接将未校验的键覆盖全局配置。

查询程序尚未构建时返回 `None`，不猜测标准，也不缓存该未知值。查询失败、超时或输出无效时抛出异常，不伪装成 C++17。导入框架不会启动查询程序。正常组件只读取共享配置，不能修改环境字段。

`test.py` 构建完成后会调用 `kdt_config.refresh()` 清空缓存。其他进程内重新构建 Adapter 后也可调用此方法，后续首次读取重新查询。

"""加载关键字定义并将其绑定到 Python 组件。"""

import importlib
import inspect
import sys
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, get_type_hints


_KEYWORDS_FILE = Path(__file__).resolve().parents[1] / "components" / "keywords.yml"


class KeywordError(Exception):
    """关键字注册器异常的基类。"""


class KeywordDefinitionError(KeywordError):
    """关键字定义或组件声明无效。"""


class KeywordNotFoundError(KeywordError, KeyError):
    """请求的关键字尚未注册。"""


class Keyword:
    """已绑定的关键字及其描述信息。"""

    def __init__(
        self,
        handler: Callable[..., Any],
        name: str = "",
        args: Optional[List[Mapping[str, Any]]] = None,
    ):
        self.handler = handler
        self.name = name
        self.args = list(args or [])
        self._parameters = list(inspect.signature(handler).parameters.values())
        try:
            self._type_hints = get_type_hints(handler)
        except (NameError, TypeError):
            self._type_hints = {}

    def __call__(self, *args, **kwargs):
        converted_args = list(args)
        converted_kwargs = dict(kwargs)
        for index, definition in enumerate(self.args):
            parameter = self._parameters[index]
            annotation = self._type_hints.get(parameter.name, parameter.annotation)
            positional = index < len(converted_args)
            keyword = parameter.name in converted_kwargs
            value = (
                converted_args[index]
                if positional
                else converted_kwargs.get(parameter.name)
            )

            if not positional and not keyword:
                continue

            value = self._convert_enum(value, definition, annotation)
            if positional:
                converted_args[index] = value
            elif keyword:
                converted_kwargs[parameter.name] = value
            else:
                converted_kwargs[parameter.name] = value
        return self.handler(*converted_args, **converted_kwargs)

    @staticmethod
    def _convert_enum(value: Any, definition: Mapping[str, Any], annotation: Any):
        """将用例中的 YAML 标量转换为组件声明的 Enum。"""
        argument_type = definition.get("type")
        if not isinstance(argument_type, str) or argument_type.lower() != "enum":
            return value
        if isinstance(value, annotation):
            return value
        try:
            options = definition["data"]
            index = next(
                index
                for index, option in enumerate(options)
                if type(option) is type(value) and option == value
            )
            return list(annotation)[index]
        except (StopIteration, IndexError, TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid value {value!r} for argument '{definition['name']}', "
                f"expected one of {options}"
            ) from exc


class KeywordRegistry:
    """将公开的关键字名称映射到可调用的 Python 组件。"""

    def __init__(self):
        self._keywords: Dict[str, Keyword] = {}

    def load(self):
        """从框架固定的 YAML 文件加载全部关键字定义。"""
        path = _KEYWORDS_FILE
        if not path.is_file():
            raise FileNotFoundError(f"Keyword definition file not found: {path}")

        components_dir = path.parent
        definitions = self._read_yaml(path)

        loaded: Dict[str, Keyword] = {}
        for name, definition in definitions.items():
            if not isinstance(name, str) or not name:
                raise KeywordDefinitionError("Keyword names must be non-empty strings")
            if not isinstance(definition, Mapping):
                raise KeywordDefinitionError(
                    f"Definition of keyword '{name}' must be a mapping"
                )

            entry = definition.get("component")
            if not isinstance(entry, str) or "." not in entry:
                raise KeywordDefinitionError(
                    f"Keyword '{name}' must declare component as "
                    f"'package.module.function'"
                )

            handler = self._resolve(entry, name, components_dir)
            declared_args = definition.get("args", [])
            if not isinstance(declared_args, list):
                raise KeywordDefinitionError(
                    f"Arguments of keyword '{name}' must be a list"
                )
            self._validate_args(name, handler, declared_args)

            loaded[name] = Keyword(
                handler=handler,
                name=str(definition.get("name", "")),
                args=declared_args,
            )

        self._keywords = loaded
        return self

    def call(self, name: str, *args, **kwargs):
        """通过 YAML 中公开的名称调用关键字。"""
        return self[name](*args, **kwargs)

    def names(self) -> List[str]:
        """返回所有已注册的关键字名称。"""
        return list(self._keywords)

    def __getitem__(self, name: str) -> Keyword:
        try:
            return self._keywords[name]
        except KeyError as exc:
            raise KeywordNotFoundError(f"Unknown keyword: {name}") from exc

    def __contains__(self, name: object) -> bool:
        return name in self._keywords

    def __iter__(self) -> Iterator[str]:
        return iter(self._keywords)

    @staticmethod
    def _read_yaml(path: Path) -> Mapping[str, Any]:
        try:
            import yaml
        except ImportError as exc:
            raise KeywordDefinitionError(
                "Loading keywords.yml requires PyYAML"
            ) from exc

        definitions = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(definitions, Mapping):
            raise KeywordDefinitionError(
                "The top level of keywords.yml must be a mapping"
            )
        return definitions

    @staticmethod
    def _resolve(
        entry: str,
        keyword_name: str,
        components_dir: Path,
    ) -> Callable[..., Any]:
        module_path, function_name = entry.rsplit(".", 1)
        package_root = str(components_dir.parent)
        if package_root not in sys.path:
            sys.path.insert(0, package_root)

        module_name = f"{components_dir.name}.{module_path}"
        try:
            module = importlib.import_module(module_name)
        except (ImportError, SyntaxError) as exc:
            raise KeywordDefinitionError(
                f"Keyword '{keyword_name}' cannot import module '{module_name}'"
            ) from exc

        handler = getattr(module, function_name, None)
        if not callable(handler):
            raise KeywordDefinitionError(
                f"Keyword '{keyword_name}' references non-callable component "
                f"'{entry}'"
            )
        return handler

    @staticmethod
    def _validate_args(
        keyword_name: str,
        handler: Callable[..., Any],
        declared_args: List[Mapping[str, Any]],
    ):
        signature = inspect.signature(handler)
        actual_parameters = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        try:
            type_hints = get_type_hints(handler)
        except (NameError, TypeError):
            type_hints = {}

        for index, argument in enumerate(declared_args):
            if not isinstance(argument, Mapping):
                raise KeywordDefinitionError(
                    f"Each argument of keyword '{keyword_name}' must be a mapping"
                )
            argument_name = argument.get("name")
            if not isinstance(argument_name, str) or not argument_name:
                raise KeywordDefinitionError(
                    f"Keyword '{keyword_name}' contains an invalid argument name"
                )

            argument_type = argument.get("type")
            if isinstance(argument_type, str) and argument_type.lower() == "enum":
                options = argument.get("data")
                if not isinstance(options, list) or not options:
                    raise KeywordDefinitionError(
                        f"Enum argument '{argument_name}' of keyword '{keyword_name}' "
                        "must contain a non-empty data list"
                    )
                if any(isinstance(option, (list, dict)) for option in options):
                    raise KeywordDefinitionError(
                        f"Enum options of argument '{argument_name}' must be YAML scalars"
                    )
                if len({(type(option), str(option)) for option in options}) != len(options):
                    raise KeywordDefinitionError(
                        f"Enum options of argument '{argument_name}' must be unique"
                    )
                if index < len(actual_parameters):
                    parameter = actual_parameters[index]
                    annotation = type_hints.get(parameter.name, parameter.annotation)
                    if not inspect.isclass(annotation) or not issubclass(annotation, Enum):
                        raise KeywordDefinitionError(
                            f"Enum argument '{argument_name}' of keyword '{keyword_name}' "
                            f"must bind to a Python Enum annotation"
                        )
                    enum_members = list(annotation)
                    if len(options) != len(enum_members):
                        raise KeywordDefinitionError(
                            f"Enum options of argument '{argument_name}' must have "
                            f"the same count as {annotation.__qualname__}"
                        )
        if len(declared_args) != len(actual_parameters):
            raise KeywordDefinitionError(
                f"Argument count of keyword '{keyword_name}' does not match "
                f"{handler.__qualname__}: expected {len(actual_parameters)}, "
                f"got {len(declared_args)}"
            )


keywords = KeywordRegistry().load()

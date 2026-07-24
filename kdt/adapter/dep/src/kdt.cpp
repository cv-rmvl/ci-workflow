#include "kdt.hpp"

#include <sstream>

#include <rmvl/core/util.hpp>

namespace kdt {

command_line_parser::command_line_parser(int argc, char **argv, std::string_view keys)
    : _argc(argc), _argv(argv), _keys(keys) {}

bool command_line_parser::parse() {
    _arguments.clear();
    _key_to_index.clear();

    if (_argc < 1 || _argv == nullptr)
        return false;

    std::unordered_map<std::string, std::size_t> key_to_index;
    std::istringstream stream(_keys);
    std::string key;
    std::size_t index = 1;
    while (stream >> key) {
        if (!key_to_index.emplace(key, index).second)
            return false;
        ++index;
    }

    if (static_cast<std::size_t>(_argc) < index)
        return false;

    std::vector<std::string> arguments;
    arguments.reserve(static_cast<std::size_t>(_argc));
    for (int i = 0; i < _argc; ++i) {
        if (_argv[i] == nullptr)
            return false;
        arguments.emplace_back(_argv[i]);
    }

    _arguments = std::move(arguments);
    _key_to_index = std::move(key_to_index);
    return true;
}

std::string command_line_parser::usage() const {
    std::string result = "Usage: ";
    result += (_argc > 0 && _argv != nullptr && _argv[0] != nullptr) ? _argv[0] : "<program>";

    std::istringstream stream(_keys);
    std::string key;
    while (stream >> key)
        result += " <" + key + ">";
    return result;
}

std::string command_line_parser::get(std::string_view key) const {
    const std::string key_str(key);
    auto it = _key_to_index.find(key_str);
    if (it == _key_to_index.end())
        RMVL_Error_(RMVL_StsBadArg, "Unknown command line key: \"%s\"", key_str.c_str());
    return _arguments[it->second];
}

std::string command_line_parser::get(int index) const {
    if (index < 0 || static_cast<std::size_t>(index) >= _arguments.size())
        RMVL_Error_(RMVL_StsOutOfRange, "Command line argument index out of range: %d", index);
    return _arguments[static_cast<std::size_t>(index)];
}

} // namespace kdt

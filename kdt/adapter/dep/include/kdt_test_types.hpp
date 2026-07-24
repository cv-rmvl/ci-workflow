#pragma once

#include <rmvlmsg/test/test_types.hpp>

namespace kdt {

inline rm::msg::TestTypes make_test_types() {
    rm::msg::TestTypes data{};
    data.bool_value = true;
    data.char_value = 'K';
    data.int8_value = -8;
    data.uint8_value = 8;
    data.int16_value = -1600;
    data.uint16_value = 1600;
    data.int32_value = -320000;
    data.uint32_value = 320000;
    data.int64_value = -6400000000LL;
    data.uint64_value = 6400000000ULL;
    data.float32_value = 3.25F;
    data.float64_value = -6.5;
    data.string_value = "RMVL KDT test message";
    data.fixed_values = {-1, 0, 1};
    data.dynamic_values = {"alpha", "", "omega"};
    return data;
}

inline bool valid_test_types(const rm::msg::TestTypes &data) {
    return data.bool_value && data.char_value == 'K' &&
           data.int8_value == -8 && data.uint8_value == 8 &&
           data.int16_value == -1600 && data.uint16_value == 1600 &&
           data.int32_value == -320000 && data.uint32_value == 320000 &&
           data.int64_value == -6400000000LL &&
           data.uint64_value == 6400000000ULL &&
           data.float32_value == 3.25F && data.float64_value == -6.5 &&
           data.string_value == "RMVL KDT test message" &&
           data.fixed_values == std::array<int32_t, 3>{-1, 0, 1} &&
           data.dynamic_values == std::vector<std::string>{"alpha", "", "omega"};
}

inline bool valid_test_types_round_trip(const rm::msg::TestTypes &data) {
    const auto serialized = data.serialize();
    return serialized.size() == data.compact_size() &&
           valid_test_types(rm::msg::TestTypes::deserialize(serialized.data()));
}

} // namespace kdt

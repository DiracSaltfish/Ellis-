#include <tgw/client.hpp>

#include <iostream>

int main() {
    std::cout << tgw::kClientVersion << ' ' << tgw::error_message(0) << '\n';
    return tgw::protocol::kline_wire_period(10008) == 10100 ? 0 : 1;
}


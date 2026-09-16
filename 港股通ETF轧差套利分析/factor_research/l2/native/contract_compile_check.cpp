#include "include/engine_contract.hpp"
int main() { etf_l2::EngineConfig c; return c.build_price_levels ? 1 : 0; }

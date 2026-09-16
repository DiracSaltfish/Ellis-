#include "wind_csv.hpp"
#include <charconv>
#include <iostream>
#include <stdexcept>
int main(int argc,char** argv) {
    try {
        if(argc!=8 && argc!=9){std::cerr<<"Usage: etf_l2_cli DATE CODE UNIT TRADES.csv ORDERS.csv QUOTES.csv OUTPUT_DIR [HH:MM]\n";return 64;}
        auto n=[](const char* s){std::int64_t x{};const std::string t(s);auto [p,e]=std::from_chars(t.data(),t.data()+t.size(),x);if(e!=std::errc{}||p!=t.data()+t.size()||x<=0)throw std::invalid_argument("invalid numeric argument");return x;};
        auto date=n(argv[1]),code=n(argv[2]);if(date>99999999||code>999999)throw std::invalid_argument("date/code out of range");
        etf_l2::Partition p;p.date=static_cast<std::int32_t>(date);p.security_code=static_cast<std::uint32_t>(code);p.market=code/100000==5?etf_l2::Market::Shanghai:etf_l2::Market::Shenzhen;
        etf_l2::EngineConfig c;c.pcf_creation_unit=n(argv[3]);c.session_profile="cn_etf_2026_h1";c.adapter_version="wind_gb18030_utf8_v1";
        if(argc==9)c.cutoff_time_us=etf_l2::parse_cutoff(argv[8]);
        auto d=etf_l2::load_wind_csv(argv[4],argv[5],argv[6],p,c);
        auto r=etf_l2::process_day(p,d.orders,d.trades,d.quotes,c);r.timings.parse_seconds=d.parse_seconds;
        etf_l2::write_result(r,argv[7]);std::cout<<"trades="<<d.trades.size()<<" orders="<<r.orders.size()<<" suffix_corrected_rows="<<d.suffix_corrections<<" ledger_seconds="<<r.timings.index_seconds+r.timings.replay_seconds+r.timings.aggregation_seconds<<"\n";
        return r.audit.trade_features_valid && r.audit.order_features_valid ? 0:2;
    }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
}

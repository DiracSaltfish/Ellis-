#include "engine_contract.hpp"
#include "execution_features.hpp"
#include <algorithm>
#include <functional>
#include <iostream>
#include <limits>
#include <random>
#include <stdexcept>
#include <string>
using namespace etf_l2;
namespace {
constexpr Time S=1000000,M=60*S,H=60*M,T=9*H+30*M;
int passed=0,failed=0;
#define CHECK(x) do {if(!(x))throw std::runtime_error(std::string("CHECK failed: ")+ #x +" at line "+std::to_string(__LINE__));}while(false)
void test(const std::string& name,const std::function<void()>& f){try{f();++passed;std::cout<<"PASS "<<name<<'\n';}catch(const std::exception& e){++failed;std::cerr<<"FAIL "<<name<<": "<<e.what()<<'\n';}}
template<class F> void rejects(F f){bool caught=false;try{f();}catch(const std::exception&){caught=true;}CHECK(caught);}
Partition p(bool sh=false){return {20260105,static_cast<std::uint32_t>(sh?513330:159636),sh?Market::Shanghai:Market::Shenzhen,0,false};}
EngineConfig cfg(){EngineConfig c;c.pcf_creation_unit=1000000;c.session_profile="cn_etf_2026_h1";return c;}
OrderEvent order(OrderId id,Side side,Quantity qty,Time t=T-S,bool sh=false){OrderEvent e;e.id=id;e.side=side;e.quantity=qty;e.price=10000;e.time=t;e.phase=classify_phase(p(sh),t,cfg().session_profile);e.action=sh?OrderAction::AddRestingRemainder:OrderAction::AddFull;return e;}
OrderEvent cancel(OrderId id,Side side,Quantity qty,Time t=T+2*S,bool sh=false){auto e=order(id,side,qty,t,sh);e.action=OrderAction::Cancel;return e;}
TradeEvent trade(std::uint64_t id,OrderId buy,OrderId sell,Quantity qty,Time t=T,Side side=Side::Buy,bool sh=false){TradeEvent e;e.trade_id=id;e.buy_id=buy;e.sell_id=sell;e.quantity=qty;e.price=10000;e.time=t;e.phase=classify_phase(p(sh),t,cfg().session_profile);e.aggressor=side;e.direction_evidence=DirectionEvidence::VendorFlagVerified;return e;}
DayResult run(std::vector<OrderEvent> o,std::vector<TradeEvent> t,bool sh=false,std::vector<QuoteSnapshot> q={},EngineConfig c=cfg()){return process_day(p(sh),o,t,q,c);}
const OrderSummary& find(const DayResult& r,OrderId id){auto x=std::find_if(r.orders.begin(),r.orders.end(),[&](const auto& o){return o.id==id;});CHECK(x!=r.orders.end());return *x;}
bool issue(const DayResult& r,const std::string& s){return std::any_of(r.audit.issues.begin(),r.audit.issues.end(),[&](const auto& x){return x.code==s;});}
}
int main(){
 test("SZ lifecycle and single-count market turnover",[]{auto r=run({order(1,Side::Buy,1000),order(2,Side::Sell,600),cancel(1,Side::Buy,400)},{trade(10,1,2,600)});CHECK(r.audit.total_trade_quantity==600);CHECK(r.audit.total_notional_x10000==6000000);CHECK(find(r,1).filled==600);CHECK(find(r,1).remaining==0);CHECK(find(r,1).conservation_passed);CHECK(r.audit.order_features_valid);});
 test("SH initial 600k + resting400k = original1m",[]{auto r=run({order(1,Side::Sell,400000,T,true),order(2,Side::Buy,600000,T-S,true),cancel(1,Side::Sell,150000,T+2*S,true)},{trade(10,2,1,600000,T,Side::Sell,true),trade(11,3,1,250000,T+S,Side::Buy,true)},true);const auto& x=find(r,1);CHECK(x.original_quantity==1000000);CHECK(x.initial_aggressive_filled==600000);CHECK(x.filled==850000);CHECK(x.cancelled==150000);CHECK(x.remaining==0);CHECK(x.conservation_passed);CHECK(r.audit.order_features_valid);CHECK(find(r,3).quantity_evidence==QuantityEvidence::ExecutedLowerBound);CHECK(find(r,3).original_quantity==0);});
 test("SH no A does not invent original size",[]{auto r=run({order(2,Side::Sell,600000,T-S,true)},{trade(10,1,2,600000,T,Side::Buy,true)},true);CHECK(find(r,1).original_quantity==0);CHECK(find(r,1).original_quantity_lower_bound==600000);CHECK(!find(r,1).conservation_passed);CHECK(r.audit.order_features_valid);});
 test("SH auction BS does not inflate original",[]{auto t=9*H+25*M+430000;auto r=run({order(1,Side::Buy,1000,9*H+15*M,true),order(2,Side::Sell,1000,9*H+15*M,true)},{trade(10,1,2,1000,t,Side::Buy,true)},true);CHECK(find(r,1).original_quantity==1000);CHECK(find(r,1).initial_aggressive_filled==0);CHECK(find(r,1).auction_filled==1000);CHECK(r.bursts.empty());});
 test("SH late continuous partial fill reconstruction",[]{auto t=14*H+59*M+55*S;auto r=run({order(1,Side::Sell,40,t,true),order(2,Side::Buy,60,T,true),cancel(1,Side::Sell,40,t+S,true)},{trade(10,2,1,60,t,Side::Sell,true)},true);CHECK(find(r,1).original_quantity==100);CHECK(find(r,1).remaining==0);CHECK(r.bursts.size()==1);});
 test("SZ late auction has no active flow",[]{auto r=run({order(1,Side::Buy,100),order(2,Side::Sell,100)},{trade(10,1,2,100,15*H)});CHECK(r.bursts.empty());CHECK(r.minutes.back().auction==100);CHECK(r.minutes.back().active_buy==0);CHECK(r.minutes.back().completed_minute==900);});
 test("duplicate trade idempotent",[]{auto t=trade(10,1,2,100);auto r=run({order(1,Side::Buy,100),order(2,Side::Sell,100)},{t,t});CHECK(r.audit.total_trade_quantity==100);CHECK(r.audit.duplicate_trades==1);CHECK(r.bursts[0].trades==1);});
 test("conflicting trade id rejects",[]{auto a=trade(10,1,2,100),b=a;b.quantity=90;rejects([&]{run({}, {a,b});});});
 test("duplicate add idempotent",[]{auto o=order(1,Side::Buy,100);auto r=run({o,o},{});CHECK(r.audit.duplicate_orders==1);CHECK(r.orders.size()==1);});
 test("conflicting add rejects",[]{rejects([]{run({order(1,Side::Buy,100),order(1,Side::Buy,200)},{});});});
 test("duplicate cancellation source identity",[]{auto c=cancel(1,Side::Buy,100);c.source={2,100,1000,1};auto r=run({order(1,Side::Buy,100),c,c},{});CHECK(find(r,1).cancelled==100);CHECK(r.audit.duplicate_cancels==1);});
 test("two equal partial cancellations remain distinct",[]{auto a=cancel(1,Side::Buy,50),b=a;a.source={1,10,0,1};b.source={1,11,0,1};auto r=run({order(1,Side::Buy,100),a,b},{});CHECK(find(r,1).cancelled==100);CHECK(r.audit.duplicate_cancels==0);});
 test("missing SZ order invalidates lifecycle only",[]{auto r=run({order(2,Side::Sell,100)},{trade(10,1,2,100)});CHECK(!r.audit.order_features_valid);CHECK(r.audit.trade_features_valid);CHECK(issue(r,"missing_original_order"));});
 test("missing SH passive order is not excused",[]{auto r=run({}, {trade(10,1,2,100,T,Side::Buy,true)},true);CHECK(r.audit.unresolved_passive_references==1);CHECK(!r.audit.order_features_valid);});
 test("overfill is explicit negative remainder",[]{auto r=run({order(1,Side::Buy,50),order(2,Side::Sell,100)},{trade(10,1,2,100)});CHECK(r.audit.overfilled_orders==1);CHECK(find(r,1).remaining==-50);CHECK(!r.audit.order_features_valid);});
 test("order side collision detects missing channel",[]{rejects([]{run({order(1,Side::Buy,100),order(1,Side::Sell,100)},{});});});
 test("same millisecond and input permutation",[]{auto o=std::vector<OrderEvent>{order(1,Side::Sell,40,T,true),order(2,Side::Buy,60,T-S,true),cancel(1,Side::Sell,40,T+S,true)};auto t=std::vector<TradeEvent>{trade(10,2,1,60,T,Side::Sell,true)};auto a=run(o,t,true);std::reverse(o.begin(),o.end());auto b=run(o,t,true);CHECK(find(a,1).original_quantity==find(b,1).original_quantity);CHECK(a.audit.total_trade_quantity==b.audit.total_trade_quantity);CHECK(b.audit.ambiguous_batches>0);});
 test("passive fill timestamp before add quarantines",[]{auto r=run({order(2,Side::Sell,100,T+S,true)},{trade(10,1,2,100,T,Side::Buy,true)},true);CHECK(issue(r,"order_event_time_conflict"));CHECK(!r.audit.order_features_valid);});
 test("active trade after SH rest is a semantic conflict",[]{auto r=run({order(1,Side::Buy,100,T-S,true),order(2,Side::Sell,100,T-S,true)},{trade(10,1,2,100,T,Side::Buy,true)},true);CHECK(issue(r,"active_fill_after_resting_add"));});
 test("burst segmentation independent of unit",[]{auto ts=std::vector<TradeEvent>{trade(10,1,2,400000,T),trade(11,1,3,600000,T+100000),trade(12,4,5,900000,T+3*S)};auto c=cfg();auto a=run({},ts,false,{},c);c.pcf_creation_unit=500000;auto b=run({},ts,false,{},c);CHECK(a.bursts.size()==2);CHECK(b.bursts.size()==2);CHECK(a.bursts[0].executed==1000000);CHECK(a.bursts[0].exchange_orders==1);CHECK(a.bursts[0].nearest_basket_multiple==1);CHECK(b.bursts[0].nearest_basket_multiple==2);});
 test("direction flip and lunch break split bursts",[]{auto r=run({}, {trade(10,1,2,100,11*H+29*M+59*S),trade(11,1,2,100,13*H),trade(12,1,2,100,13*H+1000,Side::Sell)});CHECK(r.bursts.size()==3);});
 test("price range splits burst",[]{auto a=trade(10,1,2,100),b=trade(11,1,2,100,T+1000);b.price+=30;auto r=run({}, {a,b});CHECK(r.bursts.size()==2);});
 test("unknown aggressor no active money",[]{auto t=trade(10,1,2,100);t.aggressor=Side::Unknown;t.direction_evidence=DirectionEvidence::Unknown;auto r=run({order(1,Side::Buy,100),order(2,Side::Sell,100)},{t});CHECK(r.bursts.empty());CHECK(r.minutes.back().unknown_direction==100);CHECK(!r.audit.trade_features_valid);});
 test("quote reconciliation independent evidence",[]{QuoteSnapshot q;q.time=15*H;q.cumulative_trade_quantity=100;auto r=run({order(1,Side::Buy,100),order(2,Side::Sell,100)},{trade(10,1,2,100)},false,{q});CHECK(r.audit.quote_reconciled);q.cumulative_trade_quantity=99;r=run({}, {trade(10,1,2,100)},false,{q});CHECK(!r.audit.quote_reconciled);CHECK(issue(r,"quote_volume_mismatch"));});
 test("stale quote does not claim reconciliation",[]{QuoteSnapshot q;q.time=T-S;q.cumulative_trade_quantity=100;auto r=run({}, {trade(10,1,2,100)},false,{q});CHECK(!r.audit.quote_reconciled);CHECK(issue(r,"quote_before_last_trade"));});
 test("notional overflow rejects",[]{auto t=trade(10,1,2,2);t.price=std::numeric_limits<Price>::max();rejects([&]{run({}, {t});});});
 test("negative and zero invalid inputs",[]{auto t=trade(10,1,2,0);rejects([&]{run({}, {t});});auto o=order(1,Side::Buy,-1);rejects([&]{run({o},{});});});
 test("market profile validation",[]{auto wrong=p();wrong.market=Market::Shanghai;rejects([&]{process_day(wrong,{}, {},{},cfg());});auto q=p();q.date=20260706;rejects([&]{process_day(q,{}, {},{},cfg());});auto c=cfg();c.session_profile="guess";rejects([&]{run({}, {},false,{},c);});});
 test("invalid calendar date rejected",[]{auto q=p();q.date=20260231;rejects([&]{process_day(q,{}, {},{},cfg());});});
 test("explicit 2025 sessions keep SH SZ close distinction",[]{
    for(bool sh:{false,true}){auto q=p(sh);q.date=20250702;auto c=cfg();c.session_profile="cn_etf_2025";
      CHECK(classify_phase(q,14*H+45*M,c.session_profile)==Phase::Continuous);
      CHECK(classify_phase(q,14*H+57*M,c.session_profile)==(sh?Phase::Continuous:Phase::CloseAuction));
      CHECK(classify_phase(q,12*H,c.session_profile)==Phase::Break);
      auto t=trade(10,1,2,100,T,Side::Buy,sh);auto r=process_day(q,{},std::vector<TradeEvent>{t},{},c);CHECK(r.audit.total_trade_quantity==100);
      q.date=20241231;rejects([&]{process_day(q,{}, {},{},c);});q.date=20260101;rejects([&]{process_day(q,{}, {},{},c);});
    }
    auto q=p();q.date=20260706;auto c=cfg();c.session_profile="sz_etf_20260706";CHECK(classify_phase(q,14*H+57*M,c.session_profile)==Phase::CloseAuction);
    q.market=Market::Shanghai;rejects([&]{process_day(q,{}, {},{},c);});q.market=Market::Shenzhen;q.date=20260708;rejects([&]{process_day(q,{}, {},{},c);});
 });
 test("as-of excludes future add cancel trade and quote",[]{
    const Time cut=14*H+45*M;auto c=cfg();c.cutoff_time_us=cut;
    std::vector<OrderEvent> before={order(2,Side::Buy,60,T,true)};
    std::vector<TradeEvent> tape={trade(10,2,1,60,cut-S,Side::Sell,true)};
    auto a=run(before,tape,true,{},c);before.push_back(order(1,Side::Sell,40,cut+S,true));before.push_back(cancel(1,Side::Sell,40,cut+2*S,true));tape.push_back(trade(11,5,6,1000,cut+S,Side::Buy,true));
    QuoteSnapshot q;q.time=cut+S;q.cumulative_trade_quantity=1060;auto b=run(before,tape,true,{q},c);
    CHECK(a.audit.total_trade_quantity==b.audit.total_trade_quantity);CHECK(b.audit.total_trade_quantity==60);CHECK(find(b,1).quantity_evidence==QuantityEvidence::ExecutedLowerBound);CHECK(find(b,1).original_quantity==0);CHECK(b.orders.size()==a.orders.size());CHECK(!b.audit.quote_reconciled);
 });
 test("cutoff is exclusive and changes prefix",[]{auto c=cfg();c.cutoff_time_us=14*H+30*M;auto t=trade(10,1,2,100,c.cutoff_time_us);CHECK(run({}, {t},false,{},c).audit.total_trade_quantity==0);c.cutoff_time_us=14*H+45*M;CHECK(run({}, {t},false,{},c).audit.total_trade_quantity==100);});
 test("phase mismatch rejected",[]{auto t=trade(10,1,2,100);t.phase=Phase::CloseAuction;rejects([&]{run({}, {t});});});
 test("zero unit and unsupported queue rejected",[]{auto c=cfg();c.pcf_creation_unit=0;rejects([&]{run({}, {},false,{},c);});c=cfg();c.build_price_levels=true;rejects([&]{run({}, {},false,{},c);});});
 test("memory preflight guard",[]{auto c=cfg();c.memory_budget_bytes=1;rejects([&]{run({order(1,Side::Buy,100)},{},false,{},c);});});
 test("seeded property: 250 SH fill/cancel conservation and shuffle",[]{std::mt19937 rng(713);for(int i=0;i<250;i++){Quantity initial=1+rng()%100000,passive=1+rng()%100000,left=1+rng()%100000;std::vector<OrderEvent> o={order(1,Side::Sell,passive+left,T,true),order(2,Side::Buy,initial,T-S,true),cancel(1,Side::Sell,left,T+2*S,true)};std::vector<TradeEvent> t={trade(10,2,1,initial,T,Side::Sell,true),trade(11,3,1,passive,T+S,Side::Buy,true)};std::shuffle(o.begin(),o.end(),rng);std::shuffle(t.begin(),t.end(),rng);auto r=run(o,t,true);CHECK(find(r,1).original_quantity==initial+passive+left);CHECK(find(r,1).remaining==0);CHECK(r.audit.total_trade_quantity==initial+passive);CHECK(r.audit.order_features_valid);}});
 test("dated SH closing auction and report latency",[]{
    Partition q{20260902,520600,Market::Shanghai,0,false};auto c=cfg();c.session_profile="sh_etf_20260706";
    CHECK(classify_phase(q,14*H+57*M-1,c.session_profile)==Phase::Continuous);
    CHECK(classify_phase(q,14*H+57*M,c.session_profile)==Phase::CloseAuction);
    CHECK(classify_phase(q,15*H+2*S,c.session_profile)==Phase::CloseAuction);
    CHECK(classify_phase(q,15*H+M,c.session_profile)==Phase::Closed);
    auto a=order(1,Side::Buy,100,T-S,true),b=order(2,Side::Sell,100,T-S,true);auto t=trade(10,1,2,100,15*H,Side::Buy,true);t.time+=1530000;t.phase=Phase::CloseAuction;
    auto r=process_day(q,std::vector<OrderEvent>{a,b},std::vector<TradeEvent>{t},{},c);
    CHECK(r.audit.order_features_valid);CHECK(r.minutes.back().auction==100);CHECK(r.minutes.back().active_buy==0);CHECK(r.bursts.empty());
    q.date=20260705;rejects([&]{process_day(q,{}, {},{},c);});q.date=20260913;rejects([&]{process_day(q,{}, {},{},c);});q.date=20260902;q.market=Market::Shenzhen;rejects([&]{process_day(q,{}, {},{},c);});
 });
 test("execution features preserve mixed SH parent without double counting",[]{
    auto r=run({order(1,Side::Sell,400000,T,true),order(2,Side::Buy,600000,T-S,true),cancel(1,Side::Sell,150000,T+2*S,true)},{trade(10,2,1,600000,T,Side::Sell,true),trade(11,3,1,250000,T+S,Side::Buy,true)},true);
    auto f=summarize_execution(r.orders,500000);CHECK(f[1].unit_active==600000);CHECK(f[1].unit_passive==250000);CHECK(f[1].known_unit_original==1000000);CHECK(f[1].known_unit_cancelled==150000);CHECK(f[1].unit_filled_orders==1);CHECK(f[0].total==f[1].total);CHECK(f[1].strict_unit_executed==850000);
 });
 test("execution near boundary unknown original and auction exclusion",[]{
    CHECK(near_unit(495000,500000));CHECK(!near_unit(494999,500000));CHECK(!near_unit(0,500000));rejects([]{near_unit(1,0);});
    OrderSummary o;o.side=Side::Sell;o.quantity_evidence=QuantityEvidence::ExecutedLowerBound;o.continuous_active_filled=500000;o.auction_filled=200000;
    std::vector<OrderSummary> rows{o};auto f=summarize_execution(rows,500000);CHECK(f[1].total==500000);CHECK(f[1].unknown_unit_executed==500000);CHECK(f[1].known_unit_original==0);
 });
 std::cout<<passed<<" tests passed, "<<failed<<" failed\n";return failed?1:0;
}

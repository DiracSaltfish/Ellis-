#include "tgw/session.hpp"
#include <simdjson.h>
#include <fstream>
#include <iostream>
#include <csignal>
#include <regex>
#include <unistd.h>
static volatile std::sig_atomic_t stopped=0;
int main(int argc,char**argv){
 if(argc!=4)return 2;
 std::signal(SIGTERM,[](int){stopped=1;});std::signal(SIGINT,[](int){stopped=1;});
 const auto parent=getppid();
 try{
  auto cfg=tgw::load_ini_config(argv[1]);cfg.force_logout=false;cfg.ca_file=argv[3];
  tgw::Session session(cfg);auto login=session.connect_and_login();
  if(!login.authenticated){std::cerr<<"authentication_failed\n";return 3;}
  std::ifstream in(argv[2]);std::string symbol;std::vector<tgw::SubscribeItem> items;
  while(std::getline(in,symbol)){
   if(!std::regex_match(symbol,std::regex("([0-9]{6}\\.(SH|SZ)|[0-9]{5}\\.HK)")))return 4;
   bool hk=symbol.ends_with(".HK");items.push_back({symbol.ends_with(".SH")?101:102,hk?12U:10U,symbol.substr(0,hk?5:6),0});
  }
  for(size_t i=0;i<items.size();i+=100)session.subscribe({items.begin()+i,items.begin()+std::min(i+100,items.size())});
  std::cerr<<"subscribed "<<items.size()<<"\n";
  while(!stopped && getppid()==parent){try{
   auto raw=session.receive_raw_event(std::chrono::milliseconds(500));simdjson::dom::parser parser;auto root=parser.parse(raw);
   std::string_view tag=root["headers"]["tag"].get_string();if(tag!="14"&&tag!="16")continue;
   int64_t status=root["status"].get_int64();if(status!=0)continue;
   int64_t delta=root["is_delta"].get_int64();
   // Never forward authentication headers, tokens or SDK log text.
   std::cout<<"{\"tag\":\""<<tag<<"\",\"delta\":"<<delta<<",\"data\":"<<simdjson::minify(root["data"].value())<<"}\n"<<std::flush;
  }catch(const tgw::TimeoutError&){} }
  session.close();return 0;
 }catch(...){std::cerr<<"feed_failed; reconnect_required\n";return 1;}
}

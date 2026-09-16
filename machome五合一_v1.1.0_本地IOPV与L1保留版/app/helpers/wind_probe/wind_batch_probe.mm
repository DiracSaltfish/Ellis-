// In-process Wind subscription actor. ABI pinned by the supervising helper.
// API calls: main queue. Control and atomic exports: bounded serial worker.
#import <Foundation/Foundation.h>
#include <dispatch/dispatch.h>
#include <dlfcn.h>
#include <mutex>
#include <map>
#include <string>
#include <vector>
#include <chrono>
#include <unistd.h>
#include <sys/stat.h>
using Clock=std::chrono::steady_clock;
static double mono(){return std::chrono::duration<double>(Clock::now().time_since_epoch()).count();}
using Create=int64_t(*)(void*,const char*,bool);using Modify=int64_t(*)(void*,int64_t,const char*);using Stop=int64_t(*)(void*,int64_t);using Register=void(*)(void*,void*);
struct Row{std::string code;int64_t id;int latency=5000;uint64_t seq=0;int error=0;double received=0;std::vector<unsigned char> fields,bytes;bool dirty=false;};
alignas(16) static unsigned char module[0x28];
static Create createSub;static Modify modifySub;static Stop stopSub;
static std::mutex mu;static std::map<int64_t,Row> rows;static NSString *directory;static NSArray *desired=@[];static NSDictionary *intervals=@{};static NSString *instance;static NSMutableArray *timings;static NSString *lastCommand=@"",*lastError=@"";static bool started=false,busy=false;static double lastHeartbeat=0,maxLag=0;static uint64_t heartbeats=0,unknown=0;static dispatch_source_t manager,writer;
static NSString* hex(const std::vector<unsigned char>&v){NSMutableString*s=[NSMutableString stringWithCapacity:v.size()*2];for(auto x:v)[s appendFormat:@"%02x",x];return s;}
static bool atomicJSON(NSString*name,id obj){@autoreleasepool{
 NSData*d=[NSJSONSerialization dataWithJSONObject:obj options:0 error:nil];
 if(!d)return false;NSString*p=[directory stringByAppendingPathComponent:name];
 if(![d writeToFile:p options:NSDataWritingAtomic error:nil])return false;
 chmod(p.fileSystemRepresentation,0600);return true;
}}
static void callback(uint32_t id,int32_t err,const char*,const void*ptr){
 std::lock_guard<std::mutex>l(mu);auto it=rows.find(id);if(it==rows.end()){unknown++;return;}auto&r=it->second;r.seq++;r.error=err;r.received=[[NSDate date] timeIntervalSince1970];r.dirty=true;
 if(!ptr){r.fields.clear();r.bytes.clear();return;}auto p=(const unsigned char*)ptr;uint32_t fs,bs;uintptr_t fp,bp;memcpy(&fs,p+0x2c,4);memcpy(&bs,p+0x48,4);memcpy(&fp,p+0x40,sizeof(fp));memcpy(&bp,p+0x58,sizeof(bp));
 if(!fp||!bp||fs>1048576||bs>1048576){r.error=-21010;r.fields.clear();r.bytes.clear();return;}r.fields.assign((unsigned char*)fp,(unsigned char*)fp+fs);r.bytes.assign((unsigned char*)bp,(unsigned char*)bp+bs);
}
static void exportData(){@autoreleasepool{
 std::vector<Row> changed;NSMutableArray*items=[NSMutableArray array];NSString*err;NSString*command;NSArray*perf;double lag;uint64_t hb,un;double beat;bool active;
 {std::lock_guard<std::mutex>l(mu);for(auto&kv:rows){auto&r=kv.second;[items addObject:@{@"symbol":@(r.code.c_str()),@"sub_id":@(r.id),@"callback_seq":@(r.seq),@"error":@(r.error),@"latency_ms":@(r.latency)}];if(r.dirty){changed.push_back(r);}}
 err=lastError;command=lastCommand;perf=[timings copy];lag=maxLag;hb=heartbeats;beat=lastHeartbeat;un=unknown;active=busy;}
 for(auto&r:changed){NSString*code=@(r.code.c_str());if(atomicJSON([NSString stringWithFormat:@"capture_%@.json",code],@{@"instance":instance,@"requested_windcode":code,@"sub_id":@(r.id),@"source_pid":@(getpid()),@"callback_seq":@(r.seq),@"callback_epoch_ms":@((int64_t)(r.received*1000)),@"error_code":@(r.error),@"field_info":@{@"size":@(r.fields.size()),@"hex":hex(r.fields)},@"buffer_58":@{@"size":@(r.bytes.size()),@"hex":hex(r.bytes)}})){std::lock_guard<std::mutex>l(mu);auto it=rows.find(r.id);if(it!=rows.end()&&it->second.seq==r.seq)it->second.dirty=false;}}
 atomicJSON(@"state.json",@{@"protocol":@1,@"instance":instance,@"pid":@(getpid()),@"items":items,@"command":command,@"error":err?:@"",@"busy":@(active),@"operations":perf,@"heartbeat_count":@(hb),@"heartbeat_monotonic":@(beat),@"max_main_queue_lag_ms":@(lag*1000),@"unknown_callbacks":@(un),@"time":@([[NSDate date] timeIntervalSince1970])});
}}
static bool valid(NSString*c){
 if(![c isKindOfClass:NSString.class]||c.length!=9||(![c hasSuffix:@".SZ"]&&![c hasSuffix:@".SH"]))return false;
 for(int i=0;i<6;i++)if([c characterAtIndex:i]<'0'||[c characterAtIndex:i]>'9')return false;
 return true;
}
static void tick(){@autoreleasepool{
 // Subscription APIs run on Wind's main queue; disk parsing/export stays on our serial worker.
 std::string removeCode,addCode;int64_t removeId=-1;int latency=5000;
 {std::lock_guard<std::mutex>l(mu);lastHeartbeat=mono();heartbeats++;if(timings.count>256)[timings removeObjectsInRange:NSMakeRange(0,timings.count-256)];if(!lastError.length){
 for(auto&kv:rows)if(![desired containsObject:@(kv.second.code.c_str())]){removeId=kv.first;removeCode=kv.second.code;break;}
 if(removeId<0)for(NSString*c in desired){bool found=false;for(auto&kv:rows)if(kv.second.code==c.UTF8String)found=true;if(!found){addCode=c.UTF8String;latency=[intervals[c] intValue];break;}}
 }busy=removeId>=0||!addCode.empty();}
 if(removeId>=0){double t=mono();auto result=stopSub(module,removeId);std::lock_guard<std::mutex>l(mu);[timings addObject:@{@"op":@"stop",@"code":@(removeCode.c_str()),@"ms":@((mono()-t)*1000),@"result":@(result)}];if(result>=0)rows.erase(removeId);else lastError=@"stop failed; retained session";}
 else if(!addCode.empty()){
 double t=mono();std::string empty="SELECT etfbuynumber,etfbuyamount,etfbuymoney,etfsellnumber,etfsellamount,etfsellmoney FROM ETFComprehensive.WholeETFData WHERE windcode = '' LATENCY("+std::to_string(latency)+" MS)";
 auto id=createSub(module,empty.c_str(),false);if(id<0){std::lock_guard<std::mutex>l(mu);lastError=@"create failed";return;}
 {std::lock_guard<std::mutex>l(mu);rows.emplace(id,Row{addCode,id,latency});}
 std::string sql="SELECT etfbuynumber,etfbuyamount,etfbuymoney,etfsellnumber,etfsellamount,etfsellmoney FROM ETFComprehensive.WholeETFData WHERE windcode = '"+addCode+"' LATENCY("+std::to_string(latency)+" MS)";auto result=modifySub(module,id,sql.c_str());
 std::lock_guard<std::mutex>l(mu);[timings addObject:@{@"op":@"add",@"code":@(addCode.c_str()),@"ms":@((mono()-t)*1000),@"result":@(result),@"id":@(id)}];if(result<0)lastError=@"modify failed; retained session";
 }
}}
extern "C" __attribute__((visibility("default"))) int machome_batch_start(const char*path){@autoreleasepool{
 if(started)return -1;if(!path||strlen(path)>700)return -2;directory=@(path);timings=[NSMutableArray array];instance=NSUUID.UUID.UUIDString;
 void*h=dlopen("/Applications/WindPersonFree.app/Contents/Frameworks/libWind.Cosmos.TBAPI2.dylib",RTLD_NOW|RTLD_LOCAL);if(!h)return -3;
 void*init=dlsym(h,"CJAVAInit");createSub=(Create)dlsym(h,"CJAVACreateSubscription");modifySub=(Modify)dlsym(h,"CJAVAModifySubscription");stopSub=(Stop)dlsym(h,"CJAVATerminateSubscription");auto reg=(Register)dlsym(h,"CJAVARegisterQueryCallBack");Dl_info info;if(!init||!createSub||!modifySub||!stopSub||!reg||!dladdr(init,&info))return -4;// Local subscription callback context. Matches this pinned library's 0x28-byte
 // JavaAPIModule constructor; does not mutate or initialize its global module.
 memset(module,0,sizeof(module));uint32_t mode=1;memcpy(module+0x18,&mode,sizeof(mode));
 reg(module,(void*)callback);*(void**)module=(void*)callback;
 auto queue=dispatch_queue_create("ellis.wind.once-test",DISPATCH_QUEUE_SERIAL);manager=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,queue);writer=dispatch_source_create(DISPATCH_SOURCE_TYPE_TIMER,0,0,queue);
 dispatch_source_set_timer(manager,dispatch_time(DISPATCH_TIME_NOW,500*NSEC_PER_MSEC),100*NSEC_PER_MSEC,10*NSEC_PER_MSEC);
 dispatch_source_set_event_handler(manager,^{@autoreleasepool{
 NSDictionary*c=[NSJSONSerialization JSONObjectWithData:[NSData dataWithContentsOfFile:[directory stringByAppendingPathComponent:@"command.json"]]?:[NSData data] options:0 error:nil];
 if([c isKindOfClass:NSDictionary.class]&&[c[@"id"] isKindOfClass:NSString.class]){std::lock_guard<std::mutex>l(mu);if(![c[@"id"] isEqual:lastCommand]){
 NSArray*codes=c[@"symbols"];bool ok=[codes isKindOfClass:NSArray.class]&&codes.count<=256;
 NSDictionary*requested=c[@"intervals"];
 if(![requested isKindOfClass:NSDictionary.class])ok=false;
 if(ok)for(id code in codes){int ms=[requested[code] intValue];if(!valid(code)||(ms!=500&&ms!=1000&&ms!=5000&&ms!=60000)){ok=false;break;}}
 if(ok&&[NSSet setWithArray:codes].count==codes.count){desired=[codes copy];intervals=[requested copy];lastError=@"";lastCommand=c[@"id"];}else{lastError=@"invalid command rejected";lastCommand=c[@"id"];}
 }}
 NSDictionary*attr=[[NSFileManager defaultManager] attributesOfItemAtPath:[directory stringByAppendingPathComponent:@"lease"] error:nil];
 NSDate*lease=attr[NSFileModificationDate];if(!lease||-[lease timeIntervalSinceNow]>30){std::lock_guard<std::mutex>l(mu);desired=@[];lastError=@"";}
 // At most one pending main-queue task; no unbounded queue while Wind is busy.
 static bool pending=false;if(!pending){pending=true;double enqueued=mono();dispatch_async(dispatch_get_main_queue(),^{@autoreleasepool{{std::lock_guard<std::mutex>l(mu);maxLag=std::max(maxLag,mono()-enqueued);}tick();dispatch_async(queue,^{pending=false;});}});}
 }});
 dispatch_source_set_timer(writer,dispatch_time(DISPATCH_TIME_NOW,250*NSEC_PER_MSEC),250*NSEC_PER_MSEC,20*NSEC_PER_MSEC);dispatch_source_set_event_handler(writer,^{exportData();});
 dispatch_resume(manager);dispatch_resume(writer);started=true;return 0;
}}

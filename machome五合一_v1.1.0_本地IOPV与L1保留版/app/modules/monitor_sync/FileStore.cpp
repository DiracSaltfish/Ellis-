#include "FileStore.h"
#include <QCryptographicHash>
#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLockFile>
#include <QRegularExpression>
#include <QSaveFile>
#include <QUuid>
#include <stdexcept>
#ifdef Q_OS_MACOS
#include <iconv.h>
#endif
#ifdef Q_OS_UNIX
#include <fcntl.h>
#include <unistd.h>
#endif
namespace machome::sync {
namespace {
void require(bool ok, const QString &message) { if (!ok) throw std::runtime_error(message.toStdString()); }
void safePart(const QString &s) { require(QRegularExpression("^[A-Za-z0-9_-]{1,80}$").match(s).hasMatch(), "非法数据集/设备/数据源身份"); }
void validDay(const QString &s) { require(s.size()==8 && QDate::fromString(s,"yyyyMMdd").isValid(),"非法日期"); }
void safePath(const QString &path) {
    QFileInfo p(QDir::cleanPath(path));
    while (!p.filePath().isEmpty() && p.filePath()!=p.dir().absolutePath()) {
        require(!p.isSymLink() || QStringList{"/var","/tmp","/etc"}.contains(p.absoluteFilePath()),"拒绝符号链接路径"); p=QFileInfo(p.dir().absolutePath());
    }
}
void syncDirectory(const QString &path) {
#ifdef Q_OS_UNIX
    int fd=::open(QFile::encodeName(path).constData(),O_RDONLY);
    require(fd>=0,"无法打开持久化目录"); int rc=::fsync(fd); ::close(fd); require(rc==0,"目录持久化失败");
#else
    Q_UNUSED(path);
#endif
}
void writeBytes(const QString &path,const QByteArray &bytes) {
    safePath(path); const QString parent=QFileInfo(path).absolutePath();
    QStringList created;QString cursor=parent;while(!QFileInfo::exists(cursor)){created.prepend(cursor);cursor=QFileInfo(cursor).absolutePath();}
    require(QDir().mkpath(parent),"无法创建存储目录");for(const auto &dir:created)syncDirectory(QFileInfo(dir).absolutePath()); QSaveFile f(path); f.setDirectWriteFallback(false);
    require(f.open(QIODevice::WriteOnly),f.errorString()); require(f.write(bytes)==bytes.size(),"文件写入不完整");
    require(f.flush(),"文件刷新失败");
#ifdef Q_OS_UNIX
    require(::fsync(f.handle())==0,"文件持久化失败");
#endif
    require(f.commit(),f.errorString()); syncDirectory(parent);
}
QByteArray content(const QJsonObject &f) {
    if(f.value("content_base64").isNull()) return {};
    require(f.value("content_base64").isString(),"文件内容类型无效");
    auto decoded=QByteArray::fromBase64Encoding(f.value("content_base64").toString().toLatin1(),QByteArray::AbortOnBase64DecodingErrors);
    require(bool(decoded),"Base64 无效"); require(decoded.decoded.size()<=8*1024*1024,"单文件超过 8 MiB"); return decoded.decoded;
}
QString revision(const QJsonArray &files,const QString &scope) {
    QJsonArray pairs; for(const auto &v:files) {auto f=v.toObject(); if(f["scope"]==scope) pairs.append(QJsonArray{f["name"],f["sha256"]});}
    return FileStore::digest(QJsonDocument(pairs).toJson(QJsonDocument::Compact));
}
QJsonArray csvDependencies(const QByteArray &bytes) {
    // CSV scanner supports quoted commas/newlines and doubled quotes.
    QString text=QString::fromUtf8(bytes); if(text.contains(QChar::ReplacementCharacter)) {
#ifdef Q_OS_MACOS
        iconv_t codec=iconv_open("UTF-8","GB18030");require(codec!=(iconv_t)-1,"GB18030 转码不可用");
        QByteArray converted(bytes.size()*4+16,0);char *in=const_cast<char*>(bytes.constData()),*out=converted.data();size_t left=bytes.size(),space=converted.size();
        auto result=iconv(codec,&in,&left,&out,&space);iconv_close(codec);require(result!=size_t(-1)&&left==0,"TEMP 编码无效");converted.resize(converted.size()-space);text=QString::fromUtf8(converted);
#else
        throw std::runtime_error("TEMP 必须使用 UTF-8 编码");
#endif
    }
    if(text.startsWith(QChar(0xfeff))) text.remove(0,1);
    QList<QStringList> rows; QStringList row; QString field; bool quoted=false;
    for(qsizetype i=0;i<text.size();++i){auto c=text[i]; if(c=='"'){if(quoted&&i+1<text.size()&&text[i+1]=='"'){field+='"';++i;}else quoted=!quoted;}
        else if(c==','&&!quoted){row<<field;field.clear();} else if(c=='\n'&&!quoted){row<<field;rows<<row;row.clear();field.clear();} else if(c!='\r'||quoted)field+=c;}
    require(!quoted,"TEMP CSV 引号未闭合"); if(!field.isEmpty()||!row.isEmpty()){row<<field;rows<<row;}
    QSet<QString> days; if(!rows.isEmpty()){auto h=rows.takeFirst(); int key=h.indexOf("汇率归属键"),day=h.indexOf("汇率来源日期");
        if(key>=0&&day>=0)for(const auto &r:rows)if(r.size()>qMax(key,day)&&!r[key].isEmpty()){validDay(r[day]);days.insert(r[day]);}}
    auto sorted=days.values();sorted.sort();QJsonArray result;for(const auto &d:sorted)result.append(d);return result;
}
}
QStringList FileStore::dayFiles(){return {"配对汇率锁定.json","QMT成交时间.csv","屏蔽委托.csv","屏蔽配对.csv","IB委托归类.csv","国内委托手动合并.csv","国内委托自动合并忽略.csv","人工插入差价配对.csv","人工插入日内轧差.csv"};}
QStringList FileStore::sharedFiles(){return {"差价配对TEMP.csv"};}
QString FileStore::digest(const QByteArray &b){return QString::fromLatin1(QCryptographicHash::hash(b,QCryptographicHash::Sha256).toHex());}
QJsonObject FileStore::readJson(const QString &path){safePath(path);QFile f(path);require(f.open(QIODevice::ReadOnly),"无法读取 "+path);QJsonParseError e;auto d=QJsonDocument::fromJson(f.readAll(),&e);require(e.error==QJsonParseError::NoError&&d.isObject(),"JSON 损坏 "+path);return d.object();}
void FileStore::writeJson(const QString &p,const QJsonObject &o){writeBytes(p,QJsonDocument(o).toJson(QJsonDocument::Indented));}
FileStore::FileStore(QString root):root_(QDir::cleanPath(root)){require(QDir::isAbsolutePath(root_),"存储路径必须是绝对路径");safePath(root_);require(QDir().mkpath(root_),"存储目录不可用");}
void FileStore::validateSnapshot(const QJsonObject &s){
    require(s.value("version").toInt()==1,"快照版本不支持");safePart(s["source_id"].toString());validDay(s["trade_day"].toString());
    auto files=s["files"].toArray(); require(files.size()==dayFiles().size()+sharedFiles().size(),"文件白名单不完整");QSet<QString> seen;qsizetype total=0;QJsonArray refs;
    int n=0;for(const auto &v:files){require(v.isObject(),"文件对象无效");auto f=v.toObject();QString scope=f["scope"].toString(),name=f["name"].toString();
        const QString expected=n<dayFiles().size()?dayFiles()[n]:sharedFiles()[n-dayFiles().size()];
        require(name==expected&&scope==(n<dayFiles().size()?"day":"shared"),"文件白名单或顺序无效");++n;
        require(f.contains("content_base64"),"缺失内容不能表示删除");auto b=content(f);total+=b.size();require(total<=16*1024*1024,"快照超过 16 MiB");
        require(f["sha256"].toString()==(f["content_base64"].isNull()?"missing":digest(b)),"文件校验失败");
        if(scope=="shared"&&!f["content_base64"].isNull())refs=csvDependencies(b);
        if(name=="配对汇率锁定.json"&&!f["content_base64"].isNull()){QJsonParseError e;auto d=QJsonDocument::fromJson(b,&e);require(e.error==QJsonParseError::NoError&&d.isObject(),"锁定 JSON 损坏");auto o=d.object();require(o["source_id"]==s["source_id"]&&o["trade_day"]==s["trade_day"],"锁定文件归属不符");}
    }
    require(revision(files,"day")==s["day_revision"].toString()&&revision(files,"shared")==s["shared_revision"].toString(),"快照修订不符");
    require(s["referenced_days"].toArray()==refs,"TEMP 依赖清单不符");
}
QJsonObject FileStore::head(const QString &base){
    if(QFileInfo::exists(base+"/HEAD.json"))return readJson(base+"/HEAD.json");
    return {{"epoch",readJson(root_+"/server.json")["epoch"]},{"sequence",0},{"days",QJsonObject{}},{"shared",QJsonObject{}},{"operations",QJsonObject{}}};
}
QJsonObject FileStore::loadSnapshot(const QString &base,const QJsonObject &h,const QString &day){
    auto entry=h["days"].toObject()[day].toObject();require(!entry.isEmpty(),"日期尚未上传");
    auto shared=h["shared"].toObject();QJsonArray files;
    for(const QString &scope:{QString("day"),QString("shared")}){auto e=scope=="day"?entry:shared;require(QRegularExpression("^(days/[0-9]{8}|shared)/revisions/[A-Za-z0-9_-]+$").match(e["path"].toString()).hasMatch(),"版本目录无效");auto meta=readJson(base+"/"+e["path"].toString()+"/manifest.json");
        for(auto v:meta["files"].toArray()){auto f=v.toObject();require((scope=="day"?dayFiles():sharedFiles()).contains(f["name"].toString())&&f["scope"]==scope,"版本文件不在白名单");if(f["sha256"]!="missing"){QString p=base+"/"+e["path"].toString()+"/"+f["name"].toString();safePath(p);QFile in(p);require(in.open(QIODevice::ReadOnly),"版本文件缺失");auto b=in.readAll();require(digest(b)==f["sha256"].toString(),"版本文件损坏");f["content_base64"]=QString::fromLatin1(b.toBase64());}else f["content_base64"]=QJsonValue::Null; files.append(f);}}
    QJsonObject s{{"version",1},{"source_id",QFileInfo(base).fileName()},{"trade_day",day},{"day_revision",entry["hash"]},{"shared_revision",shared["hash"]},{"files",files},{"referenced_days",shared["referenced_days"]}};
    validateSnapshot(s);return s;
}
QJsonObject FileStore::dispatch(const QJsonObject &r){
    if(!QFileInfo::exists(root_+"/server.json"))writeJson(root_+"/server.json",{{"version",1},{"epoch",QUuid::createUuid().toString(QUuid::WithoutBraces)}});
    QString dataset=r["dataset_id"].toString(),source=r["source_id"].toString(),action=r["action"].toString();safePart(dataset);safePart(source);
    QString base=root_+"/datasets/"+dataset+"/"+source;safePath(base);require(QDir().mkpath(base),"无法创建账本目录");QLockFile lock(base+"/.store.lock");require(lock.tryLock(1000),"文件仓库正在使用");auto h=head(base);
    auto status=[&]{auto o=h;o.remove("operations");return o;};
    if(action=="get_heads"||action=="changes_since")return status();
    if(action=="get_operation"){auto op=h["operations"].toObject()[r["operation_id"].toString()].toObject();return {{"found",!op.isEmpty()},{"receipt",op["receipt"]}};}
    if(action=="download_day"){
        QString day=r["trade_day"].toString();validDay(day);auto s=loadSnapshot(base,h,day);QJsonArray dependencies;
        for(auto d:s["referenced_days"].toArray())if(d.toString()!=day){dependencies.append(loadSnapshot(base,h,d.toString()));}
        return {{"head",status()},{"snapshot",s},{"dependencies",dependencies}};
    }
    require(action=="upload_day","未知同步操作");auto s=r["snapshot"].toObject();validateSnapshot(s);require(s["source_id"]==source,"来源不符");
    QString op=r["operation_id"].toString();safePart(op);QString payloadHash=digest(QJsonDocument(QJsonObject{{"snapshot",s},{"dependencies",r["dependencies"].toArray()}}).toJson(QJsonDocument::Compact));auto operations=h["operations"].toObject();
    if(operations.contains(op)){auto old=operations[op].toObject();require(old["payload_hash"]==payloadHash,"操作编号已用于不同内容");return old["receipt"].toObject();}
    require(r["expected_epoch"]==h["epoch"],"CONFLICT:服务器世代变化");auto days=h["days"].toObject();QString day=s["trade_day"].toString();auto prior=days[day].toObject(),shared=h["shared"].toObject();
    require(r["expected_day_revision"].toString()==prior["revision"].toString()&&r["expected_shared_revision"].toString()==shared["revision"].toString(),"CONFLICT:服务器日期或 TEMP 已更新");
    qint64 sequence=h["sequence"].toVariant().toLongLong()+1;QString id=QString("c%1-").arg(sequence,12,10,QChar('0'))+QUuid::createUuid().toString(QUuid::Id128);
    for(const QString &scope:{QString("day"),QString("shared")}){
        QString path=scope=="day"?"days/"+day+"/revisions/"+id:"shared/revisions/"+id;QJsonArray meta;
        for(auto v:s["files"].toArray()){auto f=v.toObject();if(f["scope"]!=scope)continue;if(!f["content_base64"].isNull())writeBytes(base+"/"+path+"/"+f["name"].toString(),content(f));f.remove("content_base64");meta.append(f);}
        writeJson(base+"/"+path+"/manifest.json",{{"files",meta}});
        QJsonObject e{{"revision",id},{"path",path},{"hash",s[scope=="day"?"day_revision":"shared_revision"]}};
        if(scope=="day")days[day]=e;else{e["referenced_days"]=s["referenced_days"];shared=e;}
    }
    for(const auto &v:r["dependencies"].toArray()) {
        auto dep=v.toObject();validateSnapshot(dep);QString date=dep["trade_day"].toString();
        require(dep["source_id"]==source&&!days.contains(date),"历史依赖归属不符或已存在");
        QString path="days/"+date+"/revisions/"+id;QJsonArray meta;
        for(auto value:dep["files"].toArray()){auto f=value.toObject();if(f["scope"]!="day")continue;if(!f["content_base64"].isNull())writeBytes(base+"/"+path+"/"+f["name"].toString(),content(f));f.remove("content_base64");meta.append(f);}
        writeJson(base+"/"+path+"/manifest.json",{{"files",meta}});
        days[date]=QJsonObject{{"revision",id},{"path",path},{"hash",dep["day_revision"]}};
    }
    // Dependencies must already exist, except dates included in this commit.
    for(auto d:s["referenced_days"].toArray())require(days.contains(d.toString()),"TEMP 来源日期尚未上传："+d.toString());
    QJsonObject receipt{{"epoch",h["epoch"]},{"sequence",sequence},{"day_revision",id},{"shared_revision",id},{"operation_id",op},{"trade_day",day},{"committed_at",QDateTime::currentDateTimeUtc().toString(Qt::ISODateWithMs)}};
    operations[op]=QJsonObject{{"payload_hash",payloadHash},{"receipt",receipt}};
    h["sequence"]=sequence;h["days"]=days;h["shared"]=shared;h["operations"]=operations;h["last_commit"]=receipt;h["last_device"]=r["device_id"];
    writeJson(base+"/commits/"+id+".json",h);writeJson(base+"/HEAD.json",h);return receipt;
}
}

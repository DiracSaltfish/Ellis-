#include "modules/upload/UploadCollectors.h"

#include <QCryptographicHash>
#include <QEventLoop>
#include <QJsonDocument>
#include <QJsonParseError>
#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QRegularExpression>
#include <QTimer>
#include <QUrlQuery>
#include <QXmlStreamReader>

#include <algorithm>
#include <cmath>
#include <cstring>

namespace machome::upload {
namespace {

quint16 le16(const QByteArray &b, qsizetype p)
{
    if (p < 0 || p + 2 > b.size()) return 0;
    return quint8(b[p]) | (quint16(quint8(b[p + 1])) << 8);
}
quint32 le32(const QByteArray &b, qsizetype p)
{
    if (p < 0 || p + 4 > b.size()) return 0;
    return quint32(quint8(b[p])) | (quint32(quint8(b[p + 1])) << 8)
        | (quint32(quint8(b[p + 2])) << 16) | (quint32(quint8(b[p + 3])) << 24);
}
quint64 le64(const QByteArray &b, qsizetype p)
{
    return quint64(le32(b, p)) | (quint64(le32(b, p + 4)) << 32);
}
double leDouble(const QByteArray &b, qsizetype p)
{
    quint64 bits = le64(b, p);
    double value = 0;
    static_assert(sizeof(value) == sizeof(bits));
    memcpy(&value, &bits, sizeof(value));
    return value;
}

bool jsonNumber(const QJsonValue &value, double *out)
{
    bool ok = false;
    double number = value.isDouble() ? value.toDouble()
        : value.isString() ? value.toString().remove(u',').toDouble(&ok) : 0;
    if (value.isDouble()) ok = true;
    if (!ok || !std::isfinite(number)) return false;
    if (out) *out = number;
    return true;
}

QByteArray unwrapJsonp(QByteArray raw)
{
    raw = raw.trimmed();
    if (raw.startsWith('{') || raw.startsWith('[')) return raw;
    const qsizetype first = raw.indexOf('('), last = raw.lastIndexOf(')');
    return first >= 0 && last > first ? raw.mid(first + 1, last - first - 1) : raw;
}

QJsonDocument json(const QByteArray &raw, QString *error)
{
    QJsonParseError parse;
    const auto result = QJsonDocument::fromJson(unwrapJsonp(raw), &parse);
    if (parse.error != QJsonParseError::NoError && error)
        *error = QStringLiteral("JSON 解析失败: %1").arg(parse.errorString());
    return result;
}

QString xmlName(const QXmlStreamReader &reader)
{
    return reader.name().toString();
}

QByteArray cfbWorkbook(const QByteArray &ole, QString *error)
{
    static const QByteArray signature = QByteArray::fromHex("d0cf11e0a1b11ae1");
    auto fail = [error](const QString &value) { if (error) *error = value; return QByteArray{}; };
    if (ole.size() < 512 || !ole.startsWith(signature))
        return fail(QStringLiteral("SAFE 响应不是 OLE2 XLS"));
    const int sectorSize = 1 << le16(ole, 30);
    if (sectorSize != 512 && sectorSize != 4096)
        return fail(QStringLiteral("SAFE XLS sector size 无效"));
    auto sector = [&](quint32 id) -> QByteArray {
        const quint64 offset = (quint64(id) + 1) * quint64(sectorSize);
        return offset + sectorSize <= quint64(ole.size())
            ? ole.mid(qsizetype(offset), sectorSize) : QByteArray{};
    };
    QList<quint32> fatSectors;
    for (int i = 0; i < 109; ++i) {
        quint32 id = le32(ole, 76 + i * 4);
        if (id < 0xfffffffaU) fatSectors.append(id);
    }
    const quint32 fatCount = le32(ole, 44);
    if (fatSectors.size() < int(fatCount))
        return fail(QStringLiteral("SAFE XLS DIFAT 不完整"));
    QVector<quint32> fat;
    for (quint32 i = 0; i < fatCount; ++i) {
        const QByteArray bytes = sector(fatSectors[int(i)]);
        if (bytes.size() != sectorSize) return fail(QStringLiteral("SAFE XLS FAT 截断"));
        for (int p = 0; p < bytes.size(); p += 4) fat.append(le32(bytes, p));
    }
    auto chain = [&](quint32 first, quint64 maximum) -> QByteArray {
        QByteArray out;
        QSet<quint32> seen;
        quint32 id = first;
        while (id < 0xfffffffaU && id < quint32(fat.size()) && !seen.contains(id)
               && quint64(out.size()) < maximum) {
            seen.insert(id);
            const QByteArray bytes = sector(id);
            if (bytes.isEmpty()) return {};
            out += bytes;
            id = fat[int(id)];
        }
        return out.left(qsizetype(std::min<quint64>(maximum, quint64(out.size()))));
    };
    const QByteArray directory = chain(le32(ole, 48), 16 * 1024 * 1024);
    quint32 rootStart = 0xffffffffU; quint64 rootSize = 0;
    for (qsizetype p = 0; p + 128 <= directory.size(); p += 128) {
        if (directory[p + 66] == 5) { rootStart = le32(directory, p + 116); rootSize = le64(directory, p + 120); break; }
    }
    const quint32 miniFatFirst = le32(ole, 60), miniFatCount = le32(ole, 64);
    QVector<quint32> miniFat;
    if (miniFatCount > 0 && miniFatFirst < 0xfffffffaU) {
        const QByteArray raw = chain(miniFatFirst, quint64(miniFatCount) * sectorSize);
        for (qsizetype p = 0; p + 4 <= raw.size(); p += 4) miniFat.append(le32(raw, p));
    }
    const QByteArray rootStream = rootStart < 0xfffffffaU && rootSize > 0
        ? chain(rootStart, rootSize) : QByteArray{};
    for (qsizetype p = 0; p + 128 <= directory.size(); p += 128) {
        const int nameBytes = le16(directory, p + 64);
        if (nameBytes < 2 || nameBytes > 64 || directory[p + 66] != 2) continue;
        QString name;
        for (int n = 0; n + 1 < nameBytes - 2; n += 2)
            name.append(QChar(le16(directory, p + n)));
        if (name != QStringLiteral("Workbook") && name != QStringLiteral("Book")) continue;
        const quint32 first = le32(directory, p + 116);
        const quint64 size = le64(directory, p + 120);
        if (size < 8 || size > 64 * 1024 * 1024)
            return fail(QStringLiteral("SAFE Workbook stream size 无效"));
        QByteArray workbook;
        if (size < le32(ole, 56)) {
            const int miniSectorSize = 1 << le16(ole, 32);
            QSet<quint32> seen; quint32 id = first;
            while (id < 0xfffffffaU && id < quint32(miniFat.size()) && !seen.contains(id)
                   && quint64(workbook.size()) < size) {
                seen.insert(id); const quint64 offset = quint64(id) * miniSectorSize;
                if (offset + miniSectorSize > quint64(rootStream.size()))
                    return fail(QStringLiteral("SAFE XLS mini-stream 截断"));
                workbook += rootStream.mid(qsizetype(offset), miniSectorSize); id = miniFat[int(id)];
            }
            workbook.truncate(qsizetype(size));
        } else {
            workbook = chain(first, size);
        }
        if (quint64(workbook.size()) != size)
            return fail(QStringLiteral("SAFE Workbook stream 截断"));
        return workbook;
    }
    return fail(QStringLiteral("SAFE XLS 缺少 Workbook stream"));
}

struct BiffCells { QHash<quint64, QString> text; QHash<quint64, double> numbers; };
quint64 cellKey(int row, int col) { return (quint64(quint32(row)) << 32) | quint32(col); }

BiffCells parseBiff(const QByteArray &workbook, QString *error)
{
    BiffCells cells;
    QStringList strings;
    for (qsizetype p = 0; p + 4 <= workbook.size();) {
        const quint16 id = le16(workbook, p), size = le16(workbook, p + 2);
        if (p + 4 + size > workbook.size()) { if (error) *error = QStringLiteral("SAFE BIFF record 截断"); return {}; }
        const qsizetype d = p + 4;
        if (id == 0x00fc && size >= 8) {
            qsizetype q = d + 8, end = d + size;
            const quint32 unique = le32(workbook, d + 4);
            for (quint32 i = 0; i < unique && q + 3 <= end; ++i) {
                const int chars = le16(workbook, q); q += 2;
                const quint8 flags = quint8(workbook[q++]);
                quint16 rich = 0; quint32 ext = 0;
                if (flags & 0x08) { if (q + 2 > end) break; rich = le16(workbook, q); q += 2; }
                if (flags & 0x04) { if (q + 4 > end) break; ext = le32(workbook, q); q += 4; }
                const bool wide = flags & 0x01;
                const qsizetype bytes = qsizetype(chars) * (wide ? 2 : 1);
                if (q + bytes + rich * 4 + ext > end) break;
                QString value;
                for (int c = 0; c < chars; ++c)
                    value.append(wide ? QChar(le16(workbook, q + c * 2))
                                      : QChar::fromLatin1(workbook[q + c]));
                strings.append(value); q += bytes + rich * 4 + ext;
            }
        } else if (id == 0x00fd && size >= 10) {
            const quint32 index = le32(workbook, d + 6);
            if (index < quint32(strings.size()))
                cells.text.insert(cellKey(le16(workbook, d), le16(workbook, d + 2)), strings[int(index)]);
        } else if (id == 0x0204 && size >= 8) {
            const int length = le16(workbook, d + 6);
            if (d + 8 + length <= p + 4 + size)
                cells.text.insert(cellKey(le16(workbook, d), le16(workbook, d + 2)),
                                  QString::fromLatin1(workbook.mid(d + 8, length)));
        } else if (id == 0x0203 && size >= 14) {
            cells.numbers.insert(cellKey(le16(workbook, d), le16(workbook, d + 2)), leDouble(workbook, d + 6));
        } else if (id == 0x027e && size >= 10) {
            const quint32 rk = le32(workbook, d + 6);
            double value = 0;
            if (rk & 0x02) value = qint32(rk) >> 2;
            else { quint64 bits = quint64(rk & 0xfffffffcU) << 32; memcpy(&value, &bits, 8); }
            if (rk & 0x01) value /= 100.0;
            cells.numbers.insert(cellKey(le16(workbook, d), le16(workbook, d + 2)), value);
        } else if (id == 0x00bd && size >= 12) {
            const int row = le16(workbook, d), firstColumn = le16(workbook, d + 2);
            const int lastColumn = le16(workbook, d + size - 2);
            for (int column = firstColumn; column <= lastColumn; ++column) {
                const qsizetype item = d + 4 + (column - firstColumn) * 6;
                if (item + 6 > d + size - 2) break;
                const quint32 rk = le32(workbook, item + 2);
                double value = 0;
                if (rk & 0x02) value = qint32(rk) >> 2;
                else { quint64 bits = quint64(rk & 0xfffffffcU) << 32; memcpy(&value, &bits, 8); }
                if (rk & 0x01) value /= 100.0;
                cells.numbers.insert(cellKey(row, column), value);
            }
        }
        p += 4 + size;
    }
    return cells;
}

} // namespace

UploadHttpResponse QtUploadHttpTransport::request(const QNetworkRequest &request,
                                                   const QByteArray &method,
                                                   const QByteArray &body,
                                                   int timeoutMs)
{
    QNetworkAccessManager manager;
    QNetworkReply *reply = manager.sendCustomRequest(request, method, body);
    QEventLoop loop;
    QTimer timer; timer.setSingleShot(true);
    QObject::connect(reply, &QNetworkReply::finished, &loop, &QEventLoop::quit);
    QObject::connect(&timer, &QTimer::timeout, &loop, &QEventLoop::quit);
    timer.start(std::clamp(timeoutMs, 250, 120000));
    loop.exec();
    UploadHttpResponse result;
    if (!timer.isActive()) {
        reply->abort(); result.error = QStringLiteral("请求超时");
    } else {
        timer.stop();
        result.statusCode = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        result.contentType = reply->header(QNetworkRequest::ContentTypeHeader).toByteArray();
        result.body = reply->read(8 * 1024 * 1024 + 1);
        if (result.body.size() > 8 * 1024 * 1024) result.error = QStringLiteral("响应超过 8 MiB");
        else if (reply->error() != QNetworkReply::NoError) result.error = reply->errorString();
    }
    reply->deleteLater();
    return result;
}

UploadCollectors::UploadCollectors(std::unique_ptr<IUploadHttpTransport> transport)
    : transport_(std::move(transport))
{
    if (!transport_) transport_ = std::make_unique<QtUploadHttpTransport>();
}
void UploadCollectors::setTransport(std::unique_ptr<IUploadHttpTransport> transport)
{
    transport_ = std::move(transport);
    if (!transport_) transport_ = std::make_unique<QtUploadHttpTransport>();
}

UploadHttpResponse UploadCollectors::fetch(const QUrl &url, const QByteArray &method,
                                            const QByteArray &body,
                                            const QHash<QByteArray, QByteArray> &headers,
                                            int timeoutMs, int attempts, QString *error)
{
    if (!url.isValid() || (url.scheme() != QStringLiteral("https")
                           && url.scheme() != QStringLiteral("http"))) {
        if (error) *error = QStringLiteral("采集 URL 无效"); return {};
    }
    UploadHttpResponse response;
    for (int attempt = 1; attempt <= std::clamp(attempts, 1, 5); ++attempt) {
        QNetworkRequest request(url);
        request.setAttribute(QNetworkRequest::RedirectPolicyAttribute,
                             QNetworkRequest::NoLessSafeRedirectPolicy);
        request.setRawHeader("User-Agent", "MachomeHub-Upload/1.0");
        for (auto it = headers.cbegin(); it != headers.cend(); ++it)
            request.setRawHeader(it.key(), it.value());
        response = transport_->request(request, method, body, timeoutMs);
        const bool retryable = !response.error.isEmpty() || response.statusCode == 408
            || response.statusCode == 429 || response.statusCode >= 500;
        if (!retryable || attempt == attempts) break;
    }
    if (!response.error.isEmpty()) { if (error) *error = response.error; return response; }
    if (response.statusCode < 200 || response.statusCode >= 300) {
        if (error) *error = QStringLiteral("HTTP %1").arg(response.statusCode);
    }
    return response;
}

QJsonArray UploadCollectors::parseEastmoneyPurchaseStatus(
    const QByteArray &body, const QHash<QString, QString> &wanted,
    const QString &date, QString *error)
{
    const QByteArray needle("datas:");
    qsizetype start = body.indexOf(needle);
    if (start < 0) { if (error) *error = QStringLiteral("Eastmoney 缺少 datas"); return {}; }
    start = body.indexOf('[', start + needle.size());
    int depth = 0; bool quoted = false, escaped = false; qsizetype end = -1;
    for (qsizetype i = start; i >= 0 && i < body.size(); ++i) {
        const char c = body[i];
        if (quoted) { if (escaped) escaped = false; else if (c == '\\') escaped = true; else if (c == '"') quoted = false; continue; }
        if (c == '"') quoted = true; else if (c == '[') ++depth; else if (c == ']' && --depth == 0) { end = i; break; }
    }
    if (start < 0 || end < 0) { if (error) *error = QStringLiteral("Eastmoney datas 未闭合"); return {}; }
    const QJsonDocument doc = json(body.mid(start, end - start + 1), error);
    if (!doc.isArray()) return {};
    QJsonArray out;
    for (const auto &rowValue : doc.array()) {
        const QJsonArray row = rowValue.toArray();
        if (row.size() < 12) continue;
        const QString code = row[0].toString().trimmed();
        if (!wanted.contains(code)) continue;
        QString rawLimit = row[9].toString().remove(u',').trimmed();
        QRegularExpression re(QStringLiteral("([0-9]+(?:\\.[0-9]+)?)"));
        auto match = re.match(rawLimit); double limit = 0;
        if (match.hasMatch()) limit = match.captured(1).toDouble();
        const QString status = row[5].toString().trimmed();
        if (!status.contains(QStringLiteral("开放"))) limit = 0;
        out.append(QJsonObject{{"kind", "purchase_status"}, {"symbol", wanted.value(code)},
            {"date", date}, {"status", status}, {"daily_limit_yuan", limit},
            {"raw_limit", row[9]}, {"source", "eastmoney_purchase_status"}});
    }
    if (out.isEmpty() && error) *error = QStringLiteral("Eastmoney 未匹配跟踪基金");
    return out;
}

QJsonArray UploadCollectors::parseEastmoneyNetValues(const QByteArray &body,
                                                       const QString &symbol,
                                                       QString *error)
{
    const QJsonDocument doc = json(body, error); const QJsonObject root = doc.object();
    double ack=0;
    if (!doc.isObject() || !jsonNumber(root.value("ErrCode"),&ack) || ack != 0) { if (error && error->isEmpty()) *error = QStringLiteral("Eastmoney NAV 未 ACK"); return {}; }
    const QJsonArray rows = root.value("Data").toObject().value("LSJZList").toArray(); QJsonArray out;
    for (const auto &v : rows) { const auto r = v.toObject(); double nav = 0; const bool ok=jsonNumber(r.value("DWJZ"),&nav);
        if (ok && nav > 0 && QDate::fromString(r.value("FSRQ").toString(), Qt::ISODate).isValid())
            out.append(QJsonObject{{"kind","net_value"},{"symbol",symbol},{"date",r.value("FSRQ")},{"nav",nav},{"source","eastmoney"}}); }
    return out;
}

QJsonArray UploadCollectors::parseSzseShares(const QByteArray &body, QString *error)
{
    const QJsonDocument doc = json(body, error); if (!doc.isArray() || doc.array().isEmpty()) return {};
    const QJsonObject report = doc.array().first().toObject(); if (!report.value("error").toString().isEmpty()) { if (error) *error=report.value("error").toString(); return {}; }
    QJsonArray out; for (const auto &v:report.value("data").toArray()) { auto r=v.toObject(); double shares=0; const bool ok=jsonNumber(r.value("current_size"),&shares);
        const QString date=r.value("size_date").toString(), code=r.value("fund_code").toString();
        if(ok&&shares>0&&code.size()==6&&QDate::fromString(date,Qt::ISODate).isValid()) out.append(QJsonObject{{"kind","share_history"},{"symbol","SZ"+code},{"date",date},{"shares_10k",shares},{"source","szse_fund_size"}}); }
    return out;
}

QJsonArray UploadCollectors::parseSseShares(const QByteArray &body, const QString &fundType, QString *error)
{
    const QJsonDocument doc=json(body,error); if(!doc.isObject()) return {}; QJsonArray out;
    for(const auto &v:doc.object().value("result").toArray()) { auto r=v.toObject(); const bool etf=fundType.toUpper()=="ETF";
        QString code=r.value(etf?"SEC_CODE":"FUND_CODE").toString(); QString date=r.value(etf?"STAT_DATE":"TRADE_DATE").toString();
        if(date.size()==8&&!date.contains('-')) date=date.left(4)+"-"+date.mid(4,2)+"-"+date.mid(6,2);
        double shares=0; const bool ok=jsonNumber(r.value(etf?"TOT_VOL":"INTERNAL_VOL"),&shares);
        if(ok&&shares>0&&code.size()==6&&QDate::fromString(date,Qt::ISODate).isValid()) out.append(QJsonObject{{"kind","share_history"},{"symbol","SH"+code},{"date",date},{"shares_10k",shares},{"source",etf?"sse_etf_volume":"sse_lof_volume"}}); }
    return out;
}

QJsonArray UploadCollectors::parseSafeXls(const QByteArray &body, QString *error)
{
    const QByteArray workbook=cfbWorkbook(body,error); if(workbook.isEmpty()) return {};
    const BiffCells cells=parseBiff(workbook,error); QHash<QString,int> columns; int headerRow=-1;
    for(auto it=cells.text.cbegin();it!=cells.text.cend();++it) { const int row=int(it.key()>>32), col=int(it.key()); const QString text=it.value().trimmed();
        if(text==QStringLiteral("日期")){headerRow=row;columns["date"]=col;} else if(text==QStringLiteral("美元"))columns["USD/CNY"]=col; else if(text==QStringLiteral("港元")||text==QStringLiteral("港币"))columns["HKD/CNY"]=col; else if(text==QStringLiteral("日元"))columns["JPY/CNY"]=col; else if(text==QStringLiteral("欧元"))columns["EUR/CNY"]=col; }
    if(headerRow<0||columns.size()<2){if(error)*error=QStringLiteral("SAFE XLS 缺少日期/币种列");return{};} QJsonArray out;
    for(int row=headerRow+1;row<headerRow+5000;++row){const quint64 dateKey=cellKey(row,columns["date"]);QString date=cells.text.value(dateKey).trimmed().replace('.', '-').replace('/', '-');
        QDate parsed=QDate::fromString(date,Qt::ISODate);if(!parsed.isValid()&&cells.numbers.contains(dateKey))parsed=QDate(1899,12,30).addDays(qRound64(cells.numbers.value(dateKey)));if(!parsed.isValid())continue;
        for(const QString &pair:{QStringLiteral("USD/CNY"),QStringLiteral("HKD/CNY"),QStringLiteral("JPY/CNY"),QStringLiteral("EUR/CNY")})if(columns.contains(pair)&&cells.numbers.contains(cellKey(row,columns[pair]))){double rate=cells.numbers.value(cellKey(row,columns[pair]))/100.0;if(rate>0)out.append(QJsonObject{{"kind","fx_rate"},{"symbol",pair},{"date",parsed.toString(Qt::ISODate)},{"rate",rate},{"source","safe"}});}}
    if(out.isEmpty()&&error)*error=QStringLiteral("SAFE XLS 无有有效汇率行"); return out;
}

QJsonObject UploadCollectors::parsePcf(const QByteArray &body, const QJsonObject &definition,
                                        const QString &sourceUrl, const QString &expectedDay,
                                        QString *error)
{
    auto fail=[error](const QString&m){if(error)*error=m;return QJsonObject{};};
    const QString security=definition.value("security_id").toString(), exchange=definition.value("exchange").toString();
    QJsonObject pcf; QJsonArray components;
    if(body.trimmed().startsWith('{')) { const auto doc=json(body,error); const auto root=doc.object(), data=root.value("data").toObject();double code=0;if(!jsonNumber(root.value("code"),&code)||code!=0||data.isEmpty())return fail(QStringLiteral("PCF JSON 未 ACK"));
        double recordNumber=0;
        if(!jsonNumber(data.value("recordNumber"),&recordNumber)||recordNumber<=0)
            return fail(QStringLiteral("PCF JSON 记录数无效"));
        pcf={{"security_id",security},{"trading_day",data.value("tradingDay").toString(data.value("tradeDate").toString())},{"pre_trading_day",data.value("ptradeDate")},{"creation",data.value("ifSg").toString().contains(QStringLiteral("申购"))?"Y":"N"},{"redemption",data.value("ifSh").toString().contains(QStringLiteral("赎回"))?"Y":"N"},{"creation_redemption_unit",data.value("minShdy")},{"estimate_cash_component_cny",data.value("minYgcash")},{"nav_per_cu",data.value("minShnav")},{"component_count",qRound64(recordNumber)}};
    } else { QXmlStreamReader xml(body); QHash<QString,QString> top,item; bool inComponent=false; int declared=0, rawComponents=0;QString rootName;QSet<QString> componentKeys;
        const QJsonArray allowedValues=definition.value("allowed_sources").toArray();QSet<QString> allowedSources;for(const auto&value:allowedValues)allowedSources.insert(value.toString());
        while(!xml.atEnd()){xml.readNext();if(xml.isStartElement()){QString n=xmlName(xml);if(rootName.isEmpty())rootName=n;if(n=="Component"){inComponent=true;item.clear();++rawComponents;}else if(n!="PCFFile"&&n!="SSEPortfolioCompositionFile"){QString text=xml.readElementText().trimmed();(inComponent?item:top).insert(n,text);}}else if(xml.isEndElement()&&xmlName(xml)=="Component"){inComponent=false;bool quantityOk=false;double quantity=(exchange=="SSE"?item["Quantity"]:item["ComponentShare"]).remove(u',').toDouble(&quantityOk);if(!quantityOk)return fail(QStringLiteral("PCF 成分数量无效"));if(quantity>0){QString source=exchange=="SSE"?item["UnderlyingSecurityID"]:item["UnderlyingSecurityIDSource"],symbol=exchange=="SSE"?item["InstrumentID"]:item["UnderlyingSecurityID"],name=exchange=="SSE"?item["InstrumentName"]:item["UnderlyingSymbol"],market,currency;if(!allowedSources.isEmpty()&&!allowedSources.contains(source))return fail(QStringLiteral("PCF 成分交易所不在该基金白名单"));if(source=="9999"){market=definition.value("market").toString("US");currency=definition.value("currency").toString("USD");}else if(source=="103"){market="HK";currency="HKD";symbol=symbol.rightJustified(4,u'0');}else if(source=="101"||source=="102"){market="CN";currency="CNY";}else return fail(QStringLiteral("PCF 含不支持的交易所来源"));const QString key=market+u':'+symbol.toUpper();if(symbol.trimmed().isEmpty()||componentKeys.contains(key))return fail(QStringLiteral("PCF 成分为空或重复"));componentKeys.insert(key);components.append(QJsonObject{{"symbol",symbol},{"name",name.isEmpty()?symbol:name},{"market",market},{"currency",currency},{"quantity",quantity}});}}}
        if(xml.hasError())return fail(QStringLiteral("PCF XML 无效: ")+xml.errorString());
        const bool sse=exchange=="SSE";if(rootName!=(sse?QStringLiteral("SSEPortfolioCompositionFile"):QStringLiteral("PCFFile")))return fail(QStringLiteral("PCF XML 根节点与交易所不匹配")); declared=top.value(sse?"RecordNumber":"TotalRecordNum").toInt(); QString creation=top.value("Creation").toUpper(),redemption=top.value("Redemption").toUpper();if(sse){const QString flag=top.value("CreationRedemptionSwitch");if(flag=="0"){creation="N";redemption="N";}else if(flag=="1"){creation="Y";redemption="Y";}else if(flag=="2"){creation="Y";redemption="N";}else if(flag=="3"){creation="N";redemption="Y";}else return fail(QStringLiteral("PCF CreationRedemptionSwitch 无效"));}
        pcf={{"security_id",top.value(sse?"FundInstrumentID":"SecurityID")},{"trading_day",top.value("TradingDay")},{"pre_trading_day",top.value("PreTradingDay")},{"creation",creation},{"redemption",redemption},{"creation_redemption_unit",top.value("CreationRedemptionUnit").toDouble()},{"estimate_cash_component_cny",top.value(sse?"EstimatedCashComponent":"EstimateCashComponent").toDouble()},{"nav_per_cu",top.value("NAVperCU").toDouble()},{"component_count",components.size()},{"components",components}};
        if(declared<=0)return fail(QStringLiteral("PCF 声明记录数无效"));
        if(declared!=rawComponents)return fail(QStringLiteral("PCF 声明记录数与实际不符"));
        if(components.isEmpty())return fail(QStringLiteral("PCF 没有正数持仓成分"));
        const QString expectedUnderlying=definition.value("expected_underlying").toString();if(!sse&&!expectedUnderlying.isEmpty()&&top.value("UnderlyingSecurityID")!=expectedUnderlying)return fail(QStringLiteral("PCF 跟踪指数不匹配"));
        const int expectedCount=definition.value("expected_component_count").toInt();if(expectedCount>0&&components.size()!=expectedCount)return fail(QStringLiteral("PCF 正数持仓数量与基金基线不符"));
        const QJsonObject expectedMarkets=definition.value("expected_market_counts").toObject();for(auto it=expectedMarkets.begin();it!=expectedMarkets.end();++it){int count=0;for(const auto&value:components)if(value.toObject().value("market").toString()==it.key())++count;if(count!=it.value().toInt())return fail(QStringLiteral("PCF %1 市场成分数不符").arg(it.key()));}
    }
    auto normalizedDay=[](QString value){ value=value.trimmed(); if(value.size()==8&&!value.contains(u'-')) value=value.left(4)+u'-'+value.mid(4,2)+u'-'+value.mid(6,2); return value; };
    pcf.insert("trading_day",normalizedDay(pcf.value("trading_day").toString()));
    pcf.insert("pre_trading_day",normalizedDay(pcf.value("pre_trading_day").toString()));
    if(pcf.value("security_id").toString()!=security||pcf.value("trading_day").toString()!=expectedDay)return fail(QStringLiteral("PCF 标的/交易日不匹配"));
    if(pcf.value("creation").toString()!="Y"&&pcf.value("creation").toString()!="N")return fail(QStringLiteral("PCF 申购标志无效"));
    if(pcf.value("redemption").toString()!="Y"&&pcf.value("redemption").toString()!="N")return fail(QStringLiteral("PCF 赎回标志无效"));
    double unit=0,cash=0,nav=0;if(!jsonNumber(pcf.value("creation_redemption_unit"),&unit)||unit<=0||!jsonNumber(pcf.value("estimate_cash_component_cny"),&cash)||!jsonNumber(pcf.value("nav_per_cu"),&nav)||nav<=0)return fail(QStringLiteral("PCF unit/cash/nav 无效"));
    const double expectedUnit=definition.value("redemption_unit").toDouble();if(expectedUnit>0&&qAbs(unit-expectedUnit)>0.001)return fail(QStringLiteral("PCF 申购赎回单位不匹配"));
    pcf.insert("creation_redemption_unit",unit);pcf.insert("estimate_cash_component_cny",cash);pcf.insert("nav_per_cu",nav);pcf.insert("source_url",sourceUrl);pcf.insert("sha256",QString::fromLatin1(QCryptographicHash::hash(body,QCryptographicHash::Sha256).toHex()));
    return QJsonObject{{"kind","pcf_cache"},{"symbol",definition.value("symbol")},{"date",expectedDay},{"source","native_pcf_http"},{"pcf",pcf}};
}

} // namespace machome::upload

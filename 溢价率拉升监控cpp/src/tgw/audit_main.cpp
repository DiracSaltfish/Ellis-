#include "tgw/RawEventValidator.h"
#include "tgw/RawEventExtractor.h"

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QCryptographicHash>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QSet>
#include <QTextStream>
#include <simdjson.h>

#include <limits>
#include <string>
#include <string_view>

namespace {

struct FieldProfile {
    qint64 present = 0;
    QMap<QString, qint64> types;
    bool hasIntegerRange = false;
    qint64 integerMinimum = 0;
    qint64 integerMaximum = 0;
    bool hasStringRange = false;
    qint64 stringMinimumBytes = 0;
    qint64 stringMaximumBytes = 0;
    qint64 pipeRows = 0;
    qint64 pipeInvalidRows = 0;
    qint64 pipeMinimumItems = std::numeric_limits<qint64>::max();
    qint64 pipeMaximumItems = 0;
};

struct AuditStats {
    qint64 lines = 0;
    qint64 valid = 0;
    qint64 invalid = 0;
    qint64 full = 0;
    qint64 delta = 0;
    qint64 tag14 = 0;
    qint64 tag16 = 0;
    qint64 numericSchema = 0;
    qint64 namedSchema = 0;
    qint64 wrappedEvents = 0;
    qint64 maximumLineBytes = 0;
    QSet<QString> symbols;
    QMap<QString, qint64> errorCounts;
    QMap<QString, qint64> firstErrorLines;
    QMap<QString, FieldProfile> fields;
};

void mergeField(FieldProfile *target, const FieldProfile &source)
{
    target->present += source.present;
    for (auto it = source.types.cbegin(); it != source.types.cend(); ++it)
        target->types[it.key()] += it.value();
    if (source.hasIntegerRange) {
        if (!target->hasIntegerRange) {
            target->integerMinimum = source.integerMinimum;
            target->integerMaximum = source.integerMaximum;
        } else {
            target->integerMinimum = qMin(target->integerMinimum, source.integerMinimum);
            target->integerMaximum = qMax(target->integerMaximum, source.integerMaximum);
        }
        target->hasIntegerRange = true;
    }
    if (source.hasStringRange) {
        if (!target->hasStringRange) {
            target->stringMinimumBytes = source.stringMinimumBytes;
            target->stringMaximumBytes = source.stringMaximumBytes;
        } else {
            target->stringMinimumBytes = qMin(target->stringMinimumBytes, source.stringMinimumBytes);
            target->stringMaximumBytes = qMax(target->stringMaximumBytes, source.stringMaximumBytes);
        }
        target->hasStringRange = true;
    }
    target->pipeRows += source.pipeRows;
    target->pipeInvalidRows += source.pipeInvalidRows;
    if (source.pipeRows > 0) {
        target->pipeMinimumItems = qMin(target->pipeMinimumItems, source.pipeMinimumItems);
        target->pipeMaximumItems = qMax(target->pipeMaximumItems, source.pipeMaximumItems);
    }
}

void merge(AuditStats *target, const AuditStats &source)
{
    target->lines += source.lines;
    target->valid += source.valid;
    target->invalid += source.invalid;
    target->full += source.full;
    target->delta += source.delta;
    target->tag14 += source.tag14;
    target->tag16 += source.tag16;
    target->numericSchema += source.numericSchema;
    target->namedSchema += source.namedSchema;
    target->wrappedEvents += source.wrappedEvents;
    target->maximumLineBytes = qMax(target->maximumLineBytes, source.maximumLineBytes);
    target->symbols.unite(source.symbols);
    for (auto it = source.errorCounts.cbegin(); it != source.errorCounts.cend(); ++it)
        target->errorCounts[it.key()] += it.value();
    for (auto it = source.fields.cbegin(); it != source.fields.cend(); ++it)
        mergeField(&target->fields[it.key()], it.value());
}

QJsonArray errorArray(const AuditStats &stats)
{
    QJsonArray errors;
    for (auto it = stats.errorCounts.cbegin(); it != stats.errorCounts.cend(); ++it) {
        QJsonObject item{{QStringLiteral("reason"), it.key()},
                         {QStringLiteral("count"), it.value()}};
        const auto line = stats.firstErrorLines.constFind(it.key());
        if (line != stats.firstErrorLines.cend()) item.insert(QStringLiteral("first_line"), *line);
        errors.append(item);
    }
    return errors;
}

QJsonArray fieldArray(const AuditStats &stats)
{
    QJsonArray fields;
    for (auto it = stats.fields.cbegin(); it != stats.fields.cend(); ++it) {
        QJsonObject typeCounts;
        for (auto type = it->types.cbegin(); type != it->types.cend(); ++type)
            typeCounts.insert(type.key(), type.value());
        QJsonObject item{{QStringLiteral("field"), it.key()},
                         {QStringLiteral("present"), it->present},
                         {QStringLiteral("coverage_ppm"),
                          stats.valid == 0 ? 0 : (it->present * 1'000'000) / stats.valid},
                         {QStringLiteral("types"), typeCounts}};
        if (it->hasIntegerRange) {
            item.insert(QStringLiteral("integer_min"), it->integerMinimum);
            item.insert(QStringLiteral("integer_max"), it->integerMaximum);
        }
        if (it->hasStringRange) {
            item.insert(QStringLiteral("string_min_bytes"), it->stringMinimumBytes);
            item.insert(QStringLiteral("string_max_bytes"), it->stringMaximumBytes);
        }
        if (it->pipeRows > 0) {
            item.insert(QStringLiteral("pipe_rows"), it->pipeRows);
            item.insert(QStringLiteral("pipe_invalid_rows"), it->pipeInvalidRows);
            item.insert(QStringLiteral("pipe_min_items"), it->pipeMinimumItems);
            item.insert(QStringLiteral("pipe_max_items"), it->pipeMaximumItems);
        }
        fields.append(item);
    }
    return fields;
}

QJsonObject toJson(const AuditStats &stats, bool includeFields = false)
{
    QJsonObject result{{QStringLiteral("lines"), stats.lines},
                       {QStringLiteral("valid"), stats.valid},
                       {QStringLiteral("invalid"), stats.invalid},
                       {QStringLiteral("full"), stats.full},
                       {QStringLiteral("delta"), stats.delta},
                       {QStringLiteral("tag14"), stats.tag14},
                       {QStringLiteral("tag16"), stats.tag16},
                       {QStringLiteral("numeric_schema"), stats.numericSchema},
                       {QStringLiteral("named_schema"), stats.namedSchema},
                       {QStringLiteral("wrapped_events"), stats.wrappedEvents},
                       {QStringLiteral("distinct_symbols"), stats.symbols.size()},
                       {QStringLiteral("maximum_line_bytes"), stats.maximumLineBytes},
                       {QStringLiteral("errors"), errorArray(stats)}};
    if (includeFields) result.insert(QStringLiteral("field_profiles"), fieldArray(stats));
    return result;
}

QString lexicalType(simdjson::dom::element value)
{
    using simdjson::dom::element_type;
    switch (value.type()) {
    case element_type::ARRAY: return QStringLiteral("array");
    case element_type::OBJECT: return QStringLiteral("object");
    case element_type::INT64: return QStringLiteral("int64");
    case element_type::UINT64: return QStringLiteral("uint64");
    case element_type::DOUBLE: return QStringLiteral("double");
    case element_type::STRING: return QStringLiteral("string");
    case element_type::BOOL: return QStringLiteral("bool");
    case element_type::NULL_VALUE: return QStringLiteral("null");
    case element_type::BIGINT: return QStringLiteral("bigint");
    }
    return QStringLiteral("unknown");
}

void profileField(AuditStats *stats, const QString &path, simdjson::dom::element value,
                  bool inspectPipe = false)
{
    FieldProfile &field = stats->fields[path];
    ++field.present;
    ++field.types[lexicalType(value)];
    if (value.type() == simdjson::dom::element_type::INT64) {
        std::int64_t number = 0;
        if (!value.get_int64().get(number)) {
            const qint64 converted = static_cast<qint64>(number);
            if (!field.hasIntegerRange) {
                field.integerMinimum = converted;
                field.integerMaximum = converted;
            } else {
                field.integerMinimum = qMin(field.integerMinimum, converted);
                field.integerMaximum = qMax(field.integerMaximum, converted);
            }
            field.hasIntegerRange = true;
        }
    } else if (value.type() == simdjson::dom::element_type::STRING) {
        std::string_view text;
        if (!value.get_string().get(text)) {
            const qint64 bytes = static_cast<qint64>(text.size());
            if (!field.hasStringRange) {
                field.stringMinimumBytes = bytes;
                field.stringMaximumBytes = bytes;
            } else {
                field.stringMinimumBytes = qMin(field.stringMinimumBytes, bytes);
                field.stringMaximumBytes = qMax(field.stringMaximumBytes, bytes);
            }
            field.hasStringRange = true;
            if (inspectPipe) {
                ++field.pipeRows;
                const QStringList parts = QString::fromUtf8(text.data(),
                    static_cast<qsizetype>(text.size())).split(u'|', Qt::KeepEmptyParts);
                const qint64 items = static_cast<qint64>(parts.size());
                field.pipeMinimumItems = qMin(field.pipeMinimumItems, items);
                field.pipeMaximumItems = qMax(field.pipeMaximumItems, items);
                bool valid = true;
                for (const QString &part : parts) {
                    bool ok = false;
                    (void)part.toLongLong(&ok);
                    if (!ok) {
                        valid = false;
                        break;
                    }
                }
                if (!valid) ++field.pipeInvalidRows;
            }
        }
    }
}

void profileValidatedLine(QByteArrayView payload, AuditStats *stats)
{
    simdjson::padded_string input(std::string(payload.data(),
                                              static_cast<std::size_t>(payload.size())));
    simdjson::dom::parser parser;
    simdjson::dom::element rootElement;
    if (parser.parse(input).get(rootElement)) return;
    simdjson::dom::object root;
    if (rootElement.get_object().get(root)) return;

    simdjson::dom::element value;
    if (!root.at_key("status").get(value)) profileField(stats, QStringLiteral("status"), value);
    if (!root.at_key("is_delta").get(value)) profileField(stats, QStringLiteral("is_delta"), value);

    simdjson::dom::element headersElement;
    simdjson::dom::object headers;
    if (!root.at_key("headers").get(headersElement) && !headersElement.get_object().get(headers)
        && !headers.at_key("tag").get(value)) {
        profileField(stats, QStringLiteral("headers.tag"), value);
    }

    simdjson::dom::element dataElement;
    simdjson::dom::object data;
    if (root.at_key("data").get(dataElement) || dataElement.get_object().get(data)) return;
    for (auto entry : data) {
        const std::string_view key = entry.key;
        const QString keyText = QString::fromUtf8(key.data(), static_cast<qsizetype>(key.size()));
        const bool inspectPipe = key == "12" || key == "13" || key == "14" || key == "15";
        profileField(stats, QStringLiteral("data.") + keyText, entry.value, inspectPipe);
    }
}

bool auditFile(const QString &path, AuditStats *stats, QByteArray *digest, QString *fatalError)
{
    QFile file;
    const bool standardInput = path == QStringLiteral("-");
    bool opened = false;
    if (standardInput) {
        opened = file.open(stdin, QIODevice::ReadOnly, QFileDevice::DontCloseHandle);
    } else {
        file.setFileName(path);
        opened = file.open(QIODevice::ReadOnly);
    }
    if (!opened || !file.isOpen()) {
        *fatalError = QStringLiteral("cannot open %1: %2").arg(path, file.errorString());
        return false;
    }
    QCryptographicHash hash(QCryptographicHash::Sha256);
    for (;;) {
        QByteArray line = file.readLine();
        // Sequential devices (stdin/pipe) can report !atEnd() until the first
        // read after EOF. A null QByteArray is the actual end-of-stream marker;
        // do not turn it into a synthetic empty JSONL record.
        if (line.isNull()) break;
        hash.addData(line);
        ++stats->lines;
        stats->maximumLineBytes = qMax(stats->maximumLineBytes,
                                       static_cast<qint64>(line.size()));
        while (line.endsWith('\n') || line.endsWith('\r')) line.chop(1);

        premium::native_tgw::ExtractedRawEvent extracted;
        QString extractionError;
        if (!premium::native_tgw::extractRawEvent(QByteArrayView(line), &extracted,
                                                   &extractionError)) {
            ++stats->invalid;
            ++stats->errorCounts[extractionError];
            if (!stats->firstErrorLines.contains(extractionError))
                stats->firstErrorLines.insert(extractionError, stats->lines);
            continue;
        }
        if (extracted.wrapped) ++stats->wrappedEvents;
        premium::native_tgw::RawEventMetadata metadata;
        QString error;
        if (!premium::native_tgw::inspectRawEvent(extracted.payload, &metadata, &error)) {
            if (error.isEmpty()) error = QStringLiteral("unknown validation failure");
            ++stats->invalid;
            ++stats->errorCounts[error];
            if (!stats->firstErrorLines.contains(error))
                stats->firstErrorLines.insert(error, stats->lines);
            continue;
        }
        ++stats->valid;
        metadata.isDelta ? ++stats->delta : ++stats->full;
        metadata.tag == QStringLiteral("14") ? ++stats->tag14 : ++stats->tag16;
        metadata.numericSchema ? ++stats->numericSchema : ++stats->namedSchema;
        stats->symbols.insert(metadata.symbol);
        profileValidatedLine(extracted.payload, stats);
    }
    *digest = hash.result().toHex();
    return true;
}

} // namespace

int main(int argc, char **argv)
{
    QCoreApplication application(argc, argv);
    QCoreApplication::setApplicationName(QStringLiteral("etf-premium-tgw-audit"));
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral(
        "Strict native C++ lexical-type audit for raw TGW JSONL captures"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("jsonl"),
                                 QStringLiteral("Raw TGW JSONL file(s), or '-' for decompressed stdin"),
                                 QStringLiteral("jsonl..."));
    parser.process(application);

    const QStringList paths = parser.positionalArguments();
    if (paths.isEmpty()) parser.showHelp(2);

    AuditStats aggregate;
    QJsonArray files;
    for (const QString &rawPath : paths) {
        const QString path = rawPath == QStringLiteral("-")
            ? rawPath : QFileInfo(rawPath).absoluteFilePath();
        AuditStats stats;
        QByteArray digest;
        QString fatalError;
        if (!auditFile(path, &stats, &digest, &fatalError)) {
            QTextStream(stderr) << fatalError << Qt::endl;
            return 2;
        }
        QJsonObject item = toJson(stats);
        item.insert(QStringLiteral("path"), path == QStringLiteral("-")
                                                   ? QStringLiteral("<stdin>") : path);
        item.insert(QStringLiteral("sha256"), QString::fromLatin1(digest));
        files.append(item);
        merge(&aggregate, stats);
    }

    QJsonObject result{{QStringLiteral("schema"), QStringLiteral("tgw-type-audit/v1")},
                       {QStringLiteral("validator"), QStringLiteral("simdjson-exact-token")},
                       {QStringLiteral("all_valid"), aggregate.invalid == 0},
                       {QStringLiteral("aggregate"), toJson(aggregate, true)},
                       {QStringLiteral("files"), files}};
    QTextStream(stdout) << QJsonDocument(result).toJson(QJsonDocument::Indented);
    return aggregate.invalid == 0 ? 0 : 1;
}

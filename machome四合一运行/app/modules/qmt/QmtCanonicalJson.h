#pragma once

#include <QByteArray>
#include <QJsonArray>
#include <QJsonObject>
#include <QJsonValue>
#include <QString>

namespace machome::qmt::contract {

enum class SnapshotKind {
    Orders,
    Positions,
};

// QMT emits snapshot_id as a JSON number backed by a non-negative Python int.
// Strings, booleans, fractional values and values outside JSON's exact integer
// range are intentionally rejected.
bool strictNonNegativeInteger(const QJsonValue &value, qint64 *result);

// Reproduces the production Python backend contract:
// json.dumps({"data": data, "extra": extra or {}}, ensure_ascii=False,
//            sort_keys=True, separators=(",", ":"))
//
// QJsonValue does not retain whether a JSON number was written as 0 or 0.0.
// The production record schema is therefore used to restore integer/float
// semantics before hashing. An empty return value means schema validation
// failed; error receives a diagnostic when provided.
QByteArray canonicalSnapshotJson(SnapshotKind kind, const QJsonArray &data,
                                 const QJsonObject &extra = {},
                                 QString *error = nullptr);
QByteArray snapshotChecksum(SnapshotKind kind, const QJsonArray &data,
                            const QJsonObject &extra = {},
                            QString *error = nullptr);

} // namespace machome::qmt::contract

#pragma once

#include <QByteArrayView>
#include <QString>

namespace machome::premium::tgw_helper {

struct ExtractedRawEvent {
    QByteArrayView payload;
    bool wrapped = false;
};

// Core persistence wraps the original TGW object in an "event" member. This
// scanner returns a byte view of that exact member without parse/reserialize,
// so lexical number types remain auditable. Direct TGW events are returned as-is.
bool extractRawEvent(QByteArrayView line, ExtractedRawEvent *result,
                     QString *error = nullptr);

} // namespace machome::premium::tgw_helper

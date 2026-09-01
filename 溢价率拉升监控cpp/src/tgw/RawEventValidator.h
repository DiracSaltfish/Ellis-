#pragma once

#include <QByteArrayView>
#include <QString>

namespace premium::native_tgw {

struct RawEventMetadata {
    QString tag;
    QString symbol;
    bool isDelta = false;
    bool numericSchema = false;
};

// Validates the outer push envelope and the concrete JSON types observed on
// the accepted TGW tag-14/tag-16 feeds. The original bytes are never rewritten.
bool inspectRawEvent(QByteArrayView payload, RawEventMetadata *metadata,
                     QString *error = nullptr);

} // namespace premium::native_tgw

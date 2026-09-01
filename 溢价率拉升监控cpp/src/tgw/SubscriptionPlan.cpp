#include "tgw/SubscriptionPlan.h"

#include <QRegularExpression>

namespace premium::native_tgw {

std::optional<tgw::SubscribeItem> subscriptionItemForSymbol(const QString &raw,
                                                             QString *error)
{
    const QString symbol = raw.trimmed().toUpper();
    static const QRegularExpression Domestic(QStringLiteral("^[0-9]{6}\\.(SH|SZ)$"));
    static const QRegularExpression Hkt(QStringLiteral("^[0-9]{5}\\.HK$"));
    tgw::SubscribeItem item;
    item.category_type = 0;
    if (Domestic.match(symbol).hasMatch()) {
        item.market = symbol.endsWith(QStringLiteral(".SH")) ? 101 : 102;
        item.flag = 10; // tgw_cpp maps public kSnapshot 10 to wire tag 14.
        item.security_code = symbol.left(6).toStdString();
        return item;
    }
    if (Hkt.match(symbol).hasMatch()) {
        item.market = 102; // Deep-connect HKT route is SZSE, not HKEX.
        item.flag = 12;    // tgw_cpp maps public kHKTSnapshot 12 to wire tag 16.
        item.security_code = symbol.left(5).toStdString();
        return item;
    }
    if (error) *error = QStringLiteral("invalid canonical subscription symbol: %1").arg(raw);
    return std::nullopt;
}

} // namespace premium::native_tgw

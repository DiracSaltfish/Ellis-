#pragma once

#include <QString>
#include <tgw/types.hpp>

#include <optional>

namespace machome::premium::tgw_helper {

std::optional<tgw::SubscribeItem> subscriptionItemForSymbol(const QString &symbol,
                                                             QString *error = nullptr);

} // namespace machome::premium::tgw_helper

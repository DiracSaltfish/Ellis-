#pragma once

#include <QString>
#include <tgw/types.hpp>

#include <optional>

namespace premium::native_tgw {

std::optional<tgw::SubscribeItem> subscriptionItemForSymbol(const QString &symbol,
                                                             QString *error = nullptr);

} // namespace premium::native_tgw

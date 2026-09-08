#pragma once
#include "ui/ModulePages.h"
class QPlainTextEdit;
class QLineEdit;
namespace hub {
class MonitorSyncPage final : public ModulePage {
    Q_OBJECT
public:
    explicit MonitorSyncPage(ModuleConfig config,QWidget *parent=nullptr);
    void applySnapshot(const QJsonObject &) override;
    void applyCommand(const QJsonObject &) override;
private:
    QPlainTextEdit *status_;
    QPlainTextEdit *devices_;
    QPlainTextEdit *history_;
    QLineEdit *root_;
};
}

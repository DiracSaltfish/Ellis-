#pragma once
#include "ui/ModulePages.h"
class QLabel;
class QLineEdit;
class QTableWidget;
namespace hub {
class MonitorSyncPage final : public ModulePage {
    Q_OBJECT
public:
    explicit MonitorSyncPage(ModuleConfig config,QWidget *parent=nullptr);
    void applySnapshot(const QJsonObject &) override;
    void applyCommand(const QJsonObject &) override;
protected:
    void updateBusyControls() override;
private:
    QLabel *metrics_[4]{};
    QLabel *connection_ = nullptr;
    QLabel *notice_ = nullptr;
    QLabel *operation_ = nullptr;
    QTableWidget *devices_ = nullptr;
    QTableWidget *history_ = nullptr;
    QLineEdit *root_ = nullptr;
    int port_ = 18976;
    QList<QPushButton *> controls_;
};
}

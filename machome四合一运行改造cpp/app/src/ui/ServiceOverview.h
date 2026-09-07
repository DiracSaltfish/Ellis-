#pragma once
#include <QJsonObject>
#include <QWidget>
#include <array>

class QLabel;
class QFrame;
class QTableWidget;
class QTreeWidget;
class QVBoxLayout;

namespace hub {
// A read-only projection of snapshots. This widget has no transport or control hooks.
class ServiceOverview final : public QWidget {
public:
    explicit ServiceOverview(QString adapter, QWidget *parent = nullptr);
    void applySnapshot(const QJsonObject &payload);
    void setDiagnostics(QTreeWidget *tree);
private:
    void metric(int index, const QString &caption, const QString &value, const QString &hint);
    QString adapter_;
    std::array<QLabel *, 4> captions_{};
    std::array<QLabel *, 4> values_{};
    std::array<QLabel *, 4> hints_{};
    QLabel *noticeTitle_ = nullptr;
    QLabel *noticeText_ = nullptr;
    QLabel *listTitle_ = nullptr;
    QFrame *notice_ = nullptr;
    QTableWidget *table_ = nullptr;
    std::array<QLabel *, 6> factNames_{};
    std::array<QLabel *, 6> facts_{};
    QVBoxLayout *bodyLayout_ = nullptr;
};
}

#pragma once
#include <QJsonObject>
#include <QList>
#include <QString>
#include <QWidget>
class QTableWidget;
class QPlainTextEdit;
namespace hub::ui {
// The source table remains in source order. Sorting/filtering is view-only, so
// incoming row updates and existing command callbacks keep their original IDs.
QWidget *tablePanel(QTableWidget *source, const QString &key,
                    const QString &detailLabel = {}, const QList<int> &hidden = {},
                    bool proxyView = true);
QWidget *depthPanel(QTableWidget *source, const QString &key, bool premium);
QWidget *textPanel(QPlainTextEdit *editor, bool log = false);
QWidget *symbolListPanel(QPlainTextEdit *source);
void showObject(QWidget *parent, const QString &title, const QJsonObject &object);
QTableWidget *jsonTable(const QStringList &headers);
void fillObjectTable(QTableWidget *table, const QJsonObject &object);
void fillArrayTable(QTableWidget *table, const QJsonArray &array);
}

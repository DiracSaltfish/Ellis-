#include "UiWidgets.h"
#include "UiText.h"
#include <QApplication>
#include <QClipboard>
#include <QCheckBox>
#include <QComboBox>
#include <QFileDialog>
#include <QHeaderView>
#include <QJsonArray>
#include <QJsonDocument>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QPlainTextEdit>
#include <QPushButton>
#include <QSaveFile>
#include <QScrollBar>
#include <QSettings>
#include <QSignalBlocker>
#include <QSortFilterProxyModel>
#include <QTableView>
#include <QTableWidget>
#include <QTabWidget>
#include <QTextDocument>
#include <QTimer>
#include <QVBoxLayout>
#include <QMessageBox>
#include <QRegularExpression>
#include <cmath>

namespace hub::ui {
namespace {
class TableProxy final : public QSortFilterProxyModel {
public:
    using QSortFilterProxyModel::QSortFilterProxyModel;
    bool lessThan(const QModelIndex &a, const QModelIndex &b) const override {
        auto number=[](QString s, bool *ok){
            s.remove(' '); s.remove(','); s.remove('%'); return s.toDouble(ok);
        };
        bool aa=false,bb=false;
        double x=number(a.data().toString(),&aa),y=number(b.data().toString(),&bb);
        if(aa&&bb&&std::isfinite(x)&&std::isfinite(y))return x<y;
        return QString::localeAwareCompare(a.data().toString(),b.data().toString())<0;
    }
};
QString cellText(const QJsonValue &v){
    if(v.isObject())return QString::fromUtf8(QJsonDocument(v.toObject()).toJson(QJsonDocument::Compact));
    if(v.isArray())return QString::fromUtf8(QJsonDocument(v.toArray()).toJson(QJsonDocument::Compact));
    return valueText(v);
}
void saveText(QWidget *parent, const QString &text, const QString &suggestion) {
    const auto path=QFileDialog::getSaveFileName(parent,QStringLiteral("导出当前显示内容"),suggestion);
    if(path.isEmpty())return;
    QSaveFile file(path); const auto bytes=text.toUtf8();
    if(!file.open(QIODevice::WriteOnly)||file.write(bytes)!=bytes.size()||!file.commit())
        QMessageBox::warning(parent,QStringLiteral("导出失败"),file.errorString());
}
}
QTableWidget *jsonTable(const QStringList &headers){
    auto *t=new QTableWidget(0,headers.size());t->setHorizontalHeaderLabels(headers);
    t->setEditTriggers(QAbstractItemView::NoEditTriggers);t->setSelectionBehavior(QAbstractItemView::SelectRows);
    t->verticalHeader()->hide();t->verticalHeader()->setDefaultSectionSize(34);
    t->setAlternatingRowColors(true);t->setShowGrid(false);return t;
}
QWidget *tablePanel(QTableWidget *source,const QString &key,const QString &detailLabel,const QList<int> &hidden,bool proxyView){
    auto *page=new QWidget;page->setObjectName(key+"Panel");
    auto *layout=new QVBoxLayout(page);layout->setContentsMargins(0,6,0,0);layout->setSpacing(8);
    auto *bar=new QHBoxLayout;
    auto *search=new QLineEdit;search->setObjectName(key+"Search");search->setPlaceholderText(QStringLiteral("搜索当前列表…"));search->setClearButtonEnabled(true);search->setMaximumWidth(280);search->setMinimumWidth(150);
    auto *count=new QLabel;count->setObjectName(key+"Count");
    bar->addWidget(search);bar->addWidget(count);bar->addStretch();
    auto *columns=new QPushButton(QStringLiteral("显示列"));bar->addWidget(columns);
    auto *copy=new QPushButton(QStringLiteral("复制"));bar->addWidget(copy);
    auto *exportButton=new QPushButton(QStringLiteral("导出"));bar->addWidget(exportButton);
    auto *detail=new QPushButton(detailLabel.isEmpty()?QStringLiteral("查看详情"):detailLabel);bar->addWidget(detail);
    layout->addLayout(bar);
    auto *empty=new QLabel(QStringLiteral("当前列表暂无记录；请查看服务状态或调整筛选。"));empty->setObjectName(key+"Empty");empty->setWordWrap(true);layout->addWidget(empty);
    QTableView *view=source;TableProxy *proxy=nullptr;
    if(proxyView){
        source->setParent(page);source->hide();proxy=new TableProxy(page);proxy->setSourceModel(source->model());proxy->setFilterKeyColumn(-1);proxy->setFilterCaseSensitivity(Qt::CaseInsensitive);
        view=new QTableView;view->setModel(proxy);view->setSortingEnabled(true);view->sortByColumn(-1,Qt::AscendingOrder);
    }
    if(proxyView)view->setObjectName(key+"View");view->setAlternatingRowColors(true);view->setShowGrid(false);view->setWordWrap(false);
    view->setSelectionBehavior(QAbstractItemView::SelectRows);view->setEditTriggers(QAbstractItemView::NoEditTriggers);
    view->verticalHeader()->hide();view->verticalHeader()->setDefaultSectionSize(34);
    view->horizontalHeader()->setSectionsMovable(true);view->horizontalHeader()->setStretchLastSection(true);
    for(int c=0;c<source->columnCount();++c){
        const auto label=source->horizontalHeaderItem(c)?source->horizontalHeaderItem(c)->text():QString();
        view->setColumnWidth(c,label.contains("名称")||label==QStringLiteral("任务")?180:label.contains("活动")||label.contains("时间")||label.contains("成功")||label.contains("确认")?150:label.contains("状态")?90:c==0?95:100);
        view->setColumnHidden(c,hidden.contains(c));
    }
    const QString setting="tables/"+key+"/header";
    const auto state=QSettings().value(setting).toByteArray();if(!state.isEmpty())view->horizontalHeader()->restoreState(state);
    auto persist=[view,setting]{QSettings().setValue(setting,view->horizontalHeader()->saveState());};
    QObject::connect(view->horizontalHeader(),&QHeaderView::sectionResized,page,persist);
    QObject::connect(view->horizontalHeader(),&QHeaderView::sectionMoved,page,persist);
    layout->addWidget(view,1);
    auto *refresh=new QTimer(page);refresh->setSingleShot(true);refresh->setInterval(80);
    auto update=[=]{
        int visible=0;
        if(!proxy){for(int r=0;r<source->rowCount();++r){bool match=search->text().isEmpty();for(int c=0;c<source->columnCount();++c)if(auto *i=source->item(r,c))match|=i->text().contains(search->text(),Qt::CaseInsensitive);source->setRowHidden(r,!match);if(match)++visible;}}
        else visible=proxy->rowCount();
        count->setText(QStringLiteral("%1 / %2 条").arg(visible).arg(source->rowCount()));empty->setVisible(visible==0);
    };
    QObject::connect(refresh,&QTimer::timeout,page,update);
    auto queue=[refresh]{refresh->start();};
    QObject::connect(source->model(),&QAbstractItemModel::dataChanged,page,queue);
    QObject::connect(source->model(),&QAbstractItemModel::rowsInserted,page,queue);
    QObject::connect(source->model(),&QAbstractItemModel::rowsRemoved,page,queue);
    QObject::connect(source->model(),&QAbstractItemModel::modelReset,page,queue);
    QObject::connect(search,&QLineEdit::textChanged,page,[=](const QString &s){if(proxy)proxy->setFilterFixedString(s);update();});
    auto currentRow=[=]{auto idx=view->currentIndex();return idx.isValid()?(proxy?proxy->mapToSource(idx).row():idx.row()):-1;};
    QObject::connect(view->selectionModel(),&QItemSelectionModel::currentChanged,page,[=](const QModelIndex &idx){detail->setEnabled(idx.isValid());if(proxy&&idx.isValid())source->setCurrentCell(proxy->mapToSource(idx).row(),0);});
    auto inspect=[=]{int row=currentRow();if(row<0)return;
        if(!detailLabel.isEmpty()){QMetaObject::invokeMethod(source,"cellDoubleClicked",Qt::DirectConnection,Q_ARG(int,row),Q_ARG(int,0));return;}
        QJsonObject data;for(int c=0;c<source->columnCount();++c)if(auto *i=source->item(row,c))data.insert(source->horizontalHeaderItem(c)?source->horizontalHeaderItem(c)->text():QString::number(c),i->text());
        if(auto *i=source->item(row,0)){const auto raw=QJsonDocument::fromJson(i->data(Qt::UserRole).toString().toUtf8());if(raw.isObject())data.insert(QStringLiteral("原始字段"),raw.object());}
        showObject(page,QStringLiteral("记录详情"),data);
    };
    detail->setEnabled(false);QObject::connect(detail,&QPushButton::clicked,page,inspect);
    if(proxy)QObject::connect(view,&QTableView::doubleClicked,page,[=]{inspect();});
    QObject::connect(columns,&QPushButton::clicked,page,[=]{QMenu menu;for(int c=0;c<source->columnCount();++c){auto *a=menu.addAction(source->model()->headerData(c,Qt::Horizontal).toString());a->setCheckable(true);a->setChecked(!view->isColumnHidden(c));QObject::connect(a,&QAction::toggled,&menu,[=](bool on){view->setColumnHidden(c,!on);persist();});}menu.exec(columns->mapToGlobal(QPoint(0,columns->height())));});
    auto serialize=[=](bool selected){QStringList out;auto *model=view->model();QList<int> cols;for(int j=0;j<view->horizontalHeader()->count();++j){int c=view->horizontalHeader()->logicalIndex(j);if(!view->isColumnHidden(c))cols<<c;}
        auto quoted=[](QString s){if(s.startsWith('=')||s.startsWith('+')||s.startsWith('@'))s.prepend('\'');s.replace('"',"\"\"");return '"'+s+'"';};
        QStringList head;for(int c:cols)head<<quoted(model->headerData(c,Qt::Horizontal).toString());out<<head.join(',');
        const auto chosen=view->selectionModel()->selectedRows();for(int r=0;r<model->rowCount();++r){if(view->isRowHidden(r))continue;if(selected&&!chosen.isEmpty()){bool found=false;for(const auto &i:chosen)found|=i.row()==r;if(!found)continue;}QStringList values;for(int c:cols)values<<quoted(model->index(r,c).data().toString());out<<values.join(',');}return out.join('\n');};
    QObject::connect(copy,&QPushButton::clicked,page,[=]{QApplication::clipboard()->setText(serialize(true));});
    QObject::connect(exportButton,&QPushButton::clicked,page,[=]{saveText(page,QString(QChar(0xfeff))+serialize(false),key+".csv");});
    update();return page;
}
QWidget *depthPanel(QTableWidget *source,const QString &key,bool premium){
    auto *page=new QWidget;auto *layout=new QVBoxLayout(page);layout->setContentsMargins(0,0,0,0);
    source->setParent(page);source->hide();
    auto *book=jsonTable({QStringLiteral("买档"),QStringLiteral("买价"),QStringLiteral("买量"),QStringLiteral("卖档"),QStringLiteral("卖价"),QStringLiteral("卖量")});
    book->setObjectName(key+"Depth");book->horizontalHeader()->setSectionResizeMode(QHeaderView::Stretch);
    auto *empty=new QLabel(QStringLiteral("尚未收到有效盘口；请结合登录、采集时段和数据时间检查。"));empty->setWordWrap(true);layout->addWidget(empty);layout->addWidget(book,1);
    auto *timer=new QTimer(page);timer->setSingleShot(true);timer->setInterval(80);
    auto render=[=]{
        int levels=0;
        for(int r=0;r<source->rowCount();++r){auto *side=source->item(r,0);if(!side)continue;int level=premium?side->text().mid(1).toInt():(source->item(r,1)?source->item(r,1)->text().toInt():0);levels=qMax(levels,qMin(level,50));}
        book->setRowCount(levels);for(int r=0;r<levels;++r)for(int c=0;c<6;++c){auto *i=book->item(r,c);if(!i){i=new QTableWidgetItem;book->setItem(r,c,i);}i->setText(QStringLiteral("—"));}
        for(int r=0;r<source->rowCount();++r){auto *side=source->item(r,0);if(!side)continue;int level=premium?side->text().mid(1).toInt():(source->item(r,1)?source->item(r,1)->text().toInt():0);if(level<1||level>levels)continue;const bool buy=side->text().startsWith(QStringLiteral("买"));int c=buy?0:3;book->item(level-1,c)->setText((buy?QStringLiteral("买"):QStringLiteral("卖"))+QString::number(level));for(int j=1;j<3;++j){auto *from=source->item(r,premium?j:j+1);book->item(level-1,c+j)->setText(from?from->text():QStringLiteral("—"));book->item(level-1,c+j)->setTextAlignment(Qt::AlignRight|Qt::AlignVCenter);}}
        empty->setVisible(levels==0);
    };
    QObject::connect(timer,&QTimer::timeout,page,render);auto queue=[timer]{timer->start();};
    QObject::connect(source->model(),&QAbstractItemModel::dataChanged,page,queue);QObject::connect(source->model(),&QAbstractItemModel::rowsInserted,page,queue);QObject::connect(source->model(),&QAbstractItemModel::rowsRemoved,page,queue);render();return page;
}
QWidget *textPanel(QPlainTextEdit *editor,bool log){
    auto *page=new QWidget;auto *layout=new QVBoxLayout(page);layout->setContentsMargins(0,6,0,0);auto *bar=new QHBoxLayout;
    auto *search=new QLineEdit;search->setPlaceholderText(QStringLiteral("查找文本…"));search->setClearButtonEnabled(true);bar->addWidget(search,1);
    auto *next=new QPushButton(QStringLiteral("查找下一个"));bar->addWidget(next);QObject::connect(next,&QPushButton::clicked,page,[=]{if(!editor->find(search->text())){editor->moveCursor(QTextCursor::Start);editor->find(search->text());}});QObject::connect(search,&QLineEdit::returnPressed,next,&QPushButton::click);
    auto *copy=new QPushButton(QStringLiteral("复制全部"));bar->addWidget(copy);QObject::connect(copy,&QPushButton::clicked,page,[=]{QApplication::clipboard()->setText(editor->toPlainText());});
    auto *save=new QPushButton(QStringLiteral("导出"));bar->addWidget(save);QObject::connect(save,&QPushButton::clicked,page,[=]{saveText(page,editor->toPlainText(),log?"运行日志.txt":"诊断数据.txt");});
    if(log){auto *follow=new QCheckBox(QStringLiteral("跟随最新"));follow->setChecked(true);editor->setProperty("followLatest",true);bar->addWidget(follow);QObject::connect(follow,&QCheckBox::toggled,editor,[=](bool on){editor->setProperty("followLatest",on);if(on)editor->verticalScrollBar()->setValue(editor->verticalScrollBar()->maximum());});editor->setPlaceholderText(QStringLiteral("尚无本次控制台接收的事件。这里只保留最近 500 行，不代表磁盘日志为空。"));}
    layout->addLayout(bar);layout->addWidget(editor,1);return page;
}
void fillObjectTable(QTableWidget *table,const QJsonObject &object){
    table->setRowCount(object.size());int r=0;for(auto it=object.begin();it!=object.end();++it,++r){table->setItem(r,0,new QTableWidgetItem(fieldText(it.key())));table->setItem(r,1,new QTableWidgetItem(cellText(it.value())));table->item(r,0)->setToolTip(it.key());}
}
void fillArrayTable(QTableWidget *table,const QJsonArray &array){
    QStringList keys;for(const auto &v:array)for(const auto &k:v.toObject().keys())if(!keys.contains(k))keys<<k;
    const QStringList preferred{"SecurityID","symbol","security_code","SecurityName","name","Quantity","quantity"};
    QStringList ordered;for(const auto &k:preferred)if(keys.removeOne(k))ordered<<k;ordered<<keys;keys=ordered;
    if(keys.isEmpty())keys={"symbol","name","quantity"};
    table->setColumnCount(keys.size());QStringList labels;for(const auto &k:keys)labels<<fieldText(k);table->setHorizontalHeaderLabels(labels);table->setRowCount(array.size());
    for(int r=0;r<array.size();++r){auto obj=array[r].toObject();for(int c=0;c<keys.size();++c){auto *item=new QTableWidgetItem(obj.contains(keys[c])?cellText(obj[keys[c]]):QStringLiteral("—"));if(c==0)item->setData(Qt::UserRole,QString::fromUtf8(QJsonDocument(obj).toJson(QJsonDocument::Compact)));table->setItem(r,c,item);}}
}
void showObject(QWidget *parent,const QString &title,const QJsonObject &object){
    auto *window=new QWidget(parent,Qt::Window);window->setAttribute(Qt::WA_DeleteOnClose);window->setWindowTitle(title);window->resize(900,650);auto *layout=new QVBoxLayout(window);auto *tabs=new QTabWidget;
    auto *table=jsonTable({QStringLiteral("项目"),QStringLiteral("内容")});fillObjectTable(table,object);tabs->addTab(tablePanel(table,"objectDetails"),QStringLiteral("字段详情"));
    auto *raw=new QPlainTextEdit(QString::fromUtf8(QJsonDocument(object).toJson(QJsonDocument::Indented)));raw->setReadOnly(true);tabs->addTab(textPanel(raw),QStringLiteral("原始 JSON"));layout->addWidget(tabs);window->show();
}
QWidget *symbolListPanel(QPlainTextEdit *source){
    auto *page=new QWidget;auto *layout=new QVBoxLayout(page);layout->setContentsMargins(0,0,0,0);auto *bar=new QHBoxLayout;
    auto *entry=new QLineEdit;entry->setPlaceholderText(QStringLiteral("输入代码，多个代码可用空格或逗号分隔"));bar->addWidget(entry,1);
    auto *add=new QPushButton(QStringLiteral("添加"));auto *remove=new QPushButton(QStringLiteral("移除选中"));auto *bulk=new QPushButton(QStringLiteral("批量文本"));bulk->setCheckable(true);bar->addWidget(add);bar->addWidget(remove);bar->addWidget(bulk);layout->addLayout(bar);
    auto *table=jsonTable({QStringLiteral("标的代码"),QStringLiteral("名称 / 市场")});table->setObjectName(source->objectName()+"List");layout->addWidget(tablePanel(table,source->objectName()+"Symbols"),1);
    source->setParent(page);source->setMaximumHeight(180);source->hide();layout->addWidget(source);QObject::connect(bulk,&QPushButton::toggled,source,&QWidget::setVisible);
    auto words=[](const QString &s){auto list=s.split(QRegularExpression("[\\s,，;；]+"),Qt::SkipEmptyParts);list.removeDuplicates();return list;};
    auto render=[=]{auto list=words(source->toPlainText());table->setRowCount(list.size());for(int r=0;r<list.size();++r){table->setItem(r,0,new QTableWidgetItem(list[r]));table->setItem(r,1,new QTableWidgetItem(list[r].contains('.')?list[r].section('.',-1):QStringLiteral("—")));}};
    QObject::connect(source,&QPlainTextEdit::textChanged,page,render);
    QObject::connect(add,&QPushButton::clicked,page,[=]{auto list=words(source->toPlainText());for(const auto &s:words(entry->text()))if(!list.contains(s))list<<s;source->setPlainText(list.join('\n'));source->document()->setModified(true);entry->clear();});QObject::connect(entry,&QLineEdit::returnPressed,add,&QPushButton::click);
    QObject::connect(remove,&QPushButton::clicked,page,[=]{auto list=words(source->toPlainText());int row=table->currentRow();if(row>=0&&row<list.size()){list.removeAt(row);source->setPlainText(list.join('\n'));source->document()->setModified(true);}});render();return page;
}
}

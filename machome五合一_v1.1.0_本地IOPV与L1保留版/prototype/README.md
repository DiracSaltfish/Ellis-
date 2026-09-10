# Qt 只读点击原型

这是信息架构原型，不含生产 adapter，不会连接或控制 machome。所有生命周期按钮故意禁用；“查看详情”和表格双击仅演示首页、四个模块页及非模态二级页面。

构建：

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH=/opt/homebrew/opt/qt
cmake --build build -j
./build/machome-hub-prototype
```

原型只依赖 Qt 6 Widgets。正式实现应按 [总体架构](../docs/02_总体架构与多线程设计.md) 拆出 Hub UI、Hub Agent、四个 adapter 和 schema 校验，不能把演示数据或禁用按钮直接改成 shell 命令。

